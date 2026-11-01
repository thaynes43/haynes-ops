"""CPU-only contract and supply-chain checks; no inference or weight downloads."""
import contextlib
import io
import importlib.util
from fractions import Fraction
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import provision
import run


class BackendContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"input")
        self.argv = ["--video", str(self.video), "--output", str(self.root / "output.wav"),
                     "--weights-dir", str(self.root / "models")]

    def rejected(self, *extra):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run.parse_args(self.argv + list(extra))

    def test_duration_and_seed_are_bounded(self):
        for value in ("0", "0.69", "8.01", "nan", "inf", "-inf"):
            with self.subTest(duration=value):
                self.rejected("--duration=" + value)
        for value in ("-1", "4294967296"):
            self.rejected("--seed=" + value)
        self.assertEqual(run.parse_args(self.argv + ["--duration", "8", "--seed", "0"]).seed, 0)

    @unittest.skipUnless(importlib.util.find_spec('av') and importlib.util.find_spec('torch'),
                         'Real decoder/tensor contract runs inside the image')
    def test_24fps_input_covers_last_25fps_sync_sample(self):
        modules=run.imports();av=modules['av'];np=modules['np']
        modules['torch'].set_num_threads(2)
        path=self.root/'real.mp4'
        with av.open(str(path),'w') as c:
            stream=c.add_stream('mpeg4',rate=24);stream.width=32;stream.height=32
            stream.pix_fmt='yuv420p';stream.codec_context.thread_count=2
            for i in range(24):
                frame=av.VideoFrame.from_ndarray(np.zeros((32,32,3),dtype=np.uint8),format='rgb24')
                frame.pts=i;frame.time_base=Fraction(1,24)
                for packet in stream.encode(frame):c.mux(packet)
            for packet in stream.encode():c.mux(packet)
        frames=run.video_frames(path,1.0,modules)
        self.assertEqual([len(x) for x in frames],[8,4,25])
        with self.assertRaises(ValueError):run.video_frames(path,1.1,modules)

    def test_requires_existing_video_and_absolute_paths(self):
        self.rejected("--video", "relative.mp4")
        self.rejected("--video", str(self.root / "missing.mp4"))
        self.rejected("--output", "relative.wav")
        self.rejected("--weights-dir", "relative")
        self.rejected("--output", str(self.root / "output.mp3"))

    def test_never_overwrites_audio_or_provenance(self):
        output = self.root / "output.wav"
        output.write_bytes(b"existing")
        self.rejected()
        self.assertEqual(output.read_bytes(), b"existing")
        output.unlink()
        output.with_suffix(".wav.json").write_text("{}")
        self.rejected()

    def test_asset_manifest_has_immutable_pins_and_expected_size(self):
        result = provision.manifest()
        self.assertEqual(len(result["source_revision"]), 40)
        self.assertLess(sum(asset["bytes"] for asset in result["assets"]), 20 * 1024 ** 3)
        self.assertEqual(len({asset["path"] for asset in result["assets"]}), len(result["assets"]))
        for asset in result["assets"]:
            self.assertEqual(len(asset["revision"]), 40)
            self.assertEqual(len(asset["sha256"]), 64)
            self.assertNotIn("..", Path(asset["path"]).parts)

    def test_verification_rejects_tampering_and_symlinks(self):
        artifact = self.root / "tiny.bin"
        artifact.write_bytes(b"good")
        assets = (("tiny.bin", "example/model", "a" * 40, "tiny.bin", 4, provision.sha256(artifact)),)
        with patch.object(provision, "ASSETS", assets):
            (self.root / "manifest.json").write_text(json.dumps(provision.manifest()))
            self.assertEqual(provision.verify(self.root)["schema_version"], 1)
            artifact.write_bytes(b"evil")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                provision.verify(self.root)
            artifact.unlink()
            artifact.symlink_to(self.video)
            with self.assertRaisesRegex(ValueError, "Missing or wrong-sized"):
                provision.verify(self.root)

    def test_provision_verifies_before_publication_and_preserves_existing_partial(self):
        payload = self.root / "expected"
        payload.write_bytes(b"good")
        assets = (("tiny.bin", "example/model", "a" * 40, "tiny.bin", 4, provision.sha256(payload)),)
        with (patch.object(provision, "ASSETS", assets),
              patch.object(provision.shutil, "disk_usage", return_value=SimpleNamespace(free=10*1024**3))):
            with patch.object(provision.urllib.request, "urlopen", return_value=io.BytesIO(b"evil")):
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    provision.provision(self.root)
            self.assertFalse((self.root / "tiny.bin").exists())
            partial = self.root / "tiny.bin.partial"
            self.assertFalse(partial.exists())
            partial.write_bytes(b"another process")
            with self.assertRaises(FileExistsError):
                provision.provision(self.root)
            self.assertEqual(partial.read_bytes(), b"another process")
            partial.unlink()
            with patch.object(provision.urllib.request, "urlopen", return_value=io.BytesIO(b"good")):
                provision.provision(self.root)
            self.assertEqual((self.root / "tiny.bin").read_bytes(), b"good")
            with patch.object(provision.urllib.request, "urlopen", side_effect=AssertionError("network")):
                provision.provision(self.root)  # An already verified tree is idempotent/offline.

    def test_insufficient_disk_fails_before_network(self):
        with (patch.object(provision.shutil,"disk_usage",return_value=SimpleNamespace(free=0)),
              patch.object(provision.urllib.request,"urlopen") as network,
              self.assertRaisesRegex(ValueError,'Insufficient free disk')):
            provision.provision(self.root)
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
