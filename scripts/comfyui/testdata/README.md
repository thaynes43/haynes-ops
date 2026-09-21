# testdata

`deployed/workflows/` and `deployed/api-workflows/` are **not committed**. CI
copies them here from the app's real resources before building the test image,
so `test_provision.py` parses the JSON that is actually deployed rather than a
duplicate that can drift:

```bash
res=kubernetes/main/apps/ai/stable-diffusion/comfyui/resources
mkdir -p scripts/comfyui/testdata/deployed/{workflows,api-workflows}
cp "$res"/workflows/*.json      scripts/comfyui/testdata/deployed/workflows/
cp "$res"/api-workflows/*.json  scripts/comfyui/testdata/deployed/api-workflows/
```

With the fixtures missing, the tests that need them skip. CI sets
`COMFYUI_TEST_STRICT_FIXTURES=1`, which turns that skip into a failure, so a
broken staging step cannot quietly pass the build.
