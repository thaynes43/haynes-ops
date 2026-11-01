"""Bounded CPU-only safety tests, no model imports, downloads or GPU work."""
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        service.ROOT=Path(self.temp.name);service.ACTIVE=None
        self.source='a'*32
        (service.ROOT/'jobs').mkdir();(service.ROOT/'output').mkdir()
        (service.ROOT/'output'/'source.mp4').write_bytes(b'fixture')
        (service.ROOT/'jobs'/(self.source+'.json')).write_text(json.dumps({'state':'succeeded','output':'source.mp4'}))
        self.data={'source_job_id':self.source,'prompt':'soft dry grain crunch'}

    def test_empty_free_success_is_valid_but_empty_health_fails(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b''
        with patch.object(service,'urlopen',return_value=response):
            self.assertIsNone(service.request(8188,'/free',{}))
            with self.assertRaises(json.JSONDecodeError):service.request(8080,'/health')

    def test_lease_excludes_other_process_style_handles(self):
        first=service.acquire();self.assertIsNotNone(first)
        self.assertIsNone(service.acquire());first.close()
        second=service.acquire();self.assertIsNotNone(second);second.close()

    def test_invalid_windows_and_sources_rejected(self):
        self.assertEqual(service.validate(self.data)['duration'],8)
        for field,value in [('duration',True),('duration',float('nan')),('duration',9),
                            ('start_seconds',-1),('source_job_id','../private'),('seed',True)]:
            with self.subTest(field=field,value=value),self.assertRaises((ValueError,FileNotFoundError)):
                service.validate({**self.data,field:value})
        (service.ROOT/'jobs'/(self.source+'.json')).write_text(json.dumps({'state':'succeeded','output':'../../outside.mp4'}))
        with self.assertRaises(ValueError):service.source_path(self.source)

    def test_unknown_engine_work_preserved_and_lease_released(self):
        lease=service.acquire();j={'id':'b'*32,'telemetry':[],**service.validate(self.data)}
        with (patch.object(service,'idle',side_effect=RuntimeError('Unknown engine work')),
             patch.object(service,'request') as request):
            service.generate(j,lease)
        request.assert_not_called();self.assertEqual(j['state'],'failed')
        other=service.acquire();self.assertIsNotNone(other);other.close()

    def test_timeout_kills_only_own_child(self):
        lease=service.acquire();j={'id':'c'*32,'telemetry':[],**service.validate(self.data)}
        child=MagicMock();child.pid=12345;child.poll.return_value=None
        sample={'utilization_percent':0,'memory_mib':0,'temperature_c':40}
        result=type('Result',(),{'stdout':'{"format":{"duration":"20"}}'})()
        with (patch.object(service,'idle'),patch.object(service,'gpu',return_value=sample),
             patch.object(service,'request'),patch.object(service.subprocess,'run',return_value=result),
             patch.object(service.subprocess,'Popen',return_value=child),
             patch.object(service.time,'monotonic',side_effect=[0,601]),
             patch.object(service.os,'killpg') as kill):
            service.generate(j,lease)
        kill.assert_called_once_with(12345,signal.SIGTERM)
        self.assertEqual(j['state'],'failed');self.assertIn('TimeoutError',j['error'])
        other=service.acquire();self.assertIsNotNone(other);other.close()

    def test_save_failure_still_releases_compute(self):
        lease=service.acquire();j={'id':'d'*32,'telemetry':[],**service.validate(self.data)}
        with (patch.object(service,'idle',side_effect=RuntimeError('hold')),
             patch.object(service,'save',side_effect=OSError('disk unavailable')),
             self.assertRaises(OSError)):
            service.generate(j,lease)
        other=service.acquire();self.assertIsNotNone(other);other.close()

    def test_malformed_queue_fails_closed(self):
        health={'accepting_jobs':True,'active_job':None}
        with patch.object(service,'request',side_effect=[health,{}]),self.assertRaises(RuntimeError):
            service.idle()


if __name__=='__main__':unittest.main()
