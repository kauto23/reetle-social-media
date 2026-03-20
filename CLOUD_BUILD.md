# Cloud Build — mirror `reetle-push-notifications`

Regional triggers live in **`us-central1`**. Builds use the same service account as push notifications
so IAM stays aligned (Artifact Registry, Cloud Run jobs, Secret Manager for `GITHUB_TOKEN`, etc.).

## Build config (`cloudbuild.yaml`)

- **`serviceAccount`** is set to  
  `push-notification-service@lect-io.iam.gserviceaccount.com`  
  so each run uses that identity (same pattern as specifying it on the trigger).
- **`options.logging: CLOUD_LOGGING_ONLY`** matches the requirement for user-specified build SAs.

## One-time: link the GitHub repo

1. Console → **Cloud Build** → **Repositories** (region **`us-central1`**).
2. **Connect repository** → use the same GitHub connection pattern you use elsewhere (or add this repo
   to an existing connection).
3. Note the **repository resource name**, e.g.  
   `projects/lect-io/locations/us-central1/connections/<CONNECTION>/repositories/kauto23-reetle-social-media`  
   (exact string comes from the Console after linking, or from  
   `gcloud builds repositories list --connection=<CONNECTION> --region=us-central1`).

## Create the trigger (CLI)

Replace `<REPOSITORY_RESOURCE>` with the full resource string from the step above.

```bash
gcloud builds triggers create github \
  --project=lect-io \
  --region=us-central1 \
  --name=reetle-social-media-trigger \
  --repository=<REPOSITORY_RESOURCE> \
  --branch-pattern='^main$' \
  --build-config=cloudbuild.yaml \
  --service-account='projects/lect-io/serviceAccounts/push-notification-service@lect-io.iam.gserviceaccount.com'
```

**Console alternative:** **Create trigger** → select this repo → **Cloud Build configuration** →
`cloudbuild.yaml` → branch `^main$` → region **`us-central1`** → **Service account** →
`push-notification-service@lect-io.iam.gserviceaccount.com`.

## Verify

```bash
gcloud builds triggers describe reetle-social-media-trigger \
  --project=lect-io \
  --region=us-central1 \
  --format=yaml
```

You should see `filename: cloudbuild.yaml`, `branch: ^main$`, `serviceAccount` / build SA as above,
and `resourceName` under `locations/us-central1/triggers/...`.

## Runtime (Cloud Run Job)

The **job** execution service account still needs Secret Manager access for app secrets
(`DATABASE_URL_PRODUCTION`, Facebook, `INTERNAL_API_KEY`). That is separate from the Cloud Build SA.
