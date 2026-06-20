# Library Migration API

The library migration workflow reorganizes an existing collection without
overwriting files. It is manifest-driven: planning and execution are separate,
and execution never re-scrapes metadata or changes a planned destination.

## Workflow

1. `POST /api/library-migration/inventory` inventories videos and backs up all
   NFO, image, and subtitle sidecars, plus the OpenAver config and database.
2. Run the normal OpenAver scanner/enrichment workflow when metadata needs to
   be completed.
3. `POST /api/library-migration/plan` creates `manifest.json` and `preview.csv`.
4. Review the preview and require explicit user confirmation of its `run_id`.
5. `POST /api/library-migration/apply` moves at most 20 complete video entries.
6. `POST /api/library-migration/verify` validates counts, bytes, fingerprints,
   and sidecar hashes after every batch.
7. `POST /api/library-migration/rollback` restores complete entries in reverse
   order when recovery is required.

The default destination is:

```text
<library>/<first actor>/<number>/[<number>] <title><version><part>.<ext>
```

Unrecognized files are planned under `#待人工整理`. Duplicate-number files
without an unambiguous multipart marker are placed in the review list.

## Safety guarantees

- Every source and destination must remain under the inventoried library root.
- Existing targets, changed sources, duplicate targets, and excessive paths
  block the affected batch without overwriting anything.
- Apply requires the immutable manifest and an exact `run_id` confirmation.
- The batch limit is 20 entries.
- Every successful move is journaled atomically.
- Rollback counts complete video entries, not individual file operations.
- Existing empty directories are not deleted.

The endpoints are also advertised by `GET /api/capabilities` for local AI
agents. Apply and rollback are marked as confirmation-required operations.
