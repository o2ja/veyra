# Runbooks

Recovery runbooks for the scenarios in `docs/DEPLOYMENT.md` §7.1 are in this folder:

- [`locked-out-admin.md`](locked-out-admin.md): user (or a workspace's sole admin) lost MFA; operator reset (S1 D-6).
- [`deploy-rollback.md`](deploy-rollback.md): first-time host setup, deploy, rollback.
- [`backup-restore.md`](backup-restore.md): backup, restore, restore drill (timed), WAL/PITR configuration.
- [`secret-rotation.md`](secret-rotation.md): every secret and how to rotate it.
- [`incident-tenant-data-exposure.md`](incident-tenant-data-exposure.md): suspected cross-tenant exposure.
- [`cloudflare-ip-update.md`](cloudflare-ip-update.md): refreshing `CLOUDFLARE_IP_RANGES`, origin lock.
