# Setup

One-time steps that need the owner's accounts. Never paste a token anywhere but GitHub's secrets page.

1. **Results repository.** A private repository `Anava13/quantacolony-study-results`.
2. **GitHub token** (fine-grained): resource owner Anava13; only `quantacolony-study-results`;
   repository permission **Contents: Read and write**; expiration past the end of data collection.
3. **Cloudflare** (free): open Workers & Pages once, which creates the `workers.dev` address. Create an
   API token from the **Edit Cloudflare Workers** template (Account Resources: your account; Zone
   Resources: all zones). Copy the Account ID from the Workers & Pages overview.
4. **Repository secrets** in this repository (Settings → Secrets and variables → Actions):
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `RESULTS_TOKEN` (the GitHub token).
5. **Pages:** Settings → Pages → Source: **GitHub Actions**.

After that, a push to `main` (or Actions → Deploy the study → Run workflow) deploys everything. The
workflow's log prints the upload service's address and the page's address.

## Updating the study

Change the study in the QuantaColony repository, then export it here:

    uv run python tools/study/export_web.py --site ../QuantaColony-External-Testing

Raise `STUDY_VERSION` (questions, tutorial) or the bundle version (plates) when they change, so every
results file records which version a participant used. Freeze the version once data collection starts.
