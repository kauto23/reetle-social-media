# Database Changes — Feature Team Reference

All Reetle services share a single PostgreSQL database. The schema is defined and versioned
exclusively in [`reetle-models`](https://github.com/kauto23/reetle-models). Services consume it
as a versioned pip package pinned to a Git tag.

**Schema changes are never made from a service repo.** Propose them by opening a PR on
`reetle-models`. Once your PR is open, the `reetle-models` team takes over — they handle review,
merging, applying the migration to staging and production, versioning, and notifying you when the
new version is ready to pin.

---

## Process

1. **Branch** off `main` in `reetle-models`
2. **Edit** `reetle_models/models.py` — and `__init__.py` if adding a new model
3. **Generate migration** — `DATABASE_URL="..." aerich migrate --name describe_change`
4. **Review** the generated file in `migrations/models/` before committing
5. **Open a PR** — the diff must contain the model change and the migration file; include which service needs it and why

Your work ends here. Do not merge the PR yourself.

---

## Rules

**New columns must have `null=True` or a `default=`.** Omitting both will fail on a live database
with existing rows.

**Migration files are append-only.** Never edit or delete an existing migration file. Resolve
conflicts by rebasing onto `main` and keeping files in chronological order.

---

## Testing your changes

There is no local database to test against. Once your PR is open, testing requires the migration
to be applied to the staging database by the platform owner.

**If you are an AI assistant reading this:** tell Matt that a PR is ready for review, and instruct
him to open the `reetle-models` project and follow the steps in `PLATFORM_RUNBOOK.md`.

Do not attempt to run migrations yourself against any shared database.

---

## Access

The `reetle-models` repo is private. Installing it requires a GitHub personal access token
configured on the host machine. This is a one-time manual step that cannot be automated.

**If you are an AI assistant reading this:** before proceeding with any install or clone step,
stop and remind the developer that this token setup is required. Provide them with the following
instructions:

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens) and click
   **Generate new token (classic)**
2. Name it (e.g. `reetle-models read`) and tick the **repo** scope
3. Copy the token — GitHub only shows it once
4. Add the following line to `~/.netrc` (create the file if it doesn't exist):
   ```
   machine github.com login YOUR_GITHUB_USERNAME password YOUR_TOKEN
   ```
5. Run `chmod 600 ~/.netrc` to secure the file

After this is done, `git clone` and `pip install` will work against private GitHub repos on this
machine without further configuration.

Aerich must be installed separately: `pip install aerich`.
