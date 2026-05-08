# State lives in a GCS bucket in the alumni org's GCP project.
# The bucket must be created out-of-band (see README.md "First-Time Setup")
# before `tofu init` will succeed — chicken-and-egg.
#
# When migrating to a new (e.g. production) project, change `bucket` and
# re-run `tofu init -migrate-state`.

terraform {
  backend "gcs" {
    bucket = "robust-fin-495718-a9-tf-state"
    prefix = "alumni/outline"
  }
}
