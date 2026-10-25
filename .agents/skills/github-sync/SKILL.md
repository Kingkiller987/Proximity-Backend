---
name: github-sync
description: >-
  Enforces continuous GitHub synchronization for the Proximity backend repository.
  Use after every completed logical backend code change, test, fix, or migration
  to run tests, review diffs, scan for secrets, commit, push to GitHub, and verify remote SHA.
---

# Proximity Backend — GitHub Synchronization Workflow

## Repository Boundaries & Isolation Rules
- **Backend Authoritative Repository:** Remote Debian host `kingp@192.168.86.189` at `/home/kingp/proximity-backend/`.
  - All application code, Alembic migrations, pytest tests, docker services, and git operations execute here.
  - Remote GitHub URL: `git@github.com:Kingkiller987/Proximity-Backend.git`
- **Frontend Workspace:** Local Mac `~/Documents/CodingStuff/Proximity/` — **STRICTLY READ-ONLY**.
  - NEVER edit, stage, commit, or push files in this repository.
  - Never combine frontend and backend into a monorepo or git submodule.
- **Agent Local Workspace:** Local Mac `~/Documents/CodingStuff/Proximity-Backend-Agent/`.
  - Stores only agent documentation, architecture decisions, plans, and notes.
- **Database Safety Guard:**
  - Automated tests must run strictly against `proximity_test`.
  - Never run migrations against or mutate the development database `proximity`.
  - PostgreSQL is bound to localhost (`127.0.0.1:5432`) and must never be exposed publicly.

---

## Mandatory Per-Change Sequence (Test-Review-Commit-Push-Verify)

Never proceed to a subsequent logical task before completing all 8 steps of the current change:

### Step 1: Implement the Change
- Make minimal, targeted changes on the Debian host (`/home/kingp/proximity-backend/`).
- Follow server-authoritative security principles (location freshness <= 30s, accuracy <= 50m, visit isolation, two-phase handoff).

### Step 2: Run Tests and Validations
- Execute pytest in the remote virtual environment against `proximity_test`:
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && .venv/bin/pytest -v"
  ```
- All tests must pass before proceeding. If tests fail, fix the defect immediately.

### Step 3: Review Diff and Staged Files
- Inspect working tree diff:
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && git status && git diff"
  ```
- Confirm the changes directly satisfy the requirements without unintended edits or formatting noise.

### Step 4: Secret and Credential Scanning
- Ensure no `.env`, tokens, passwords, private keys, or credentials exist in staged files:
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && git status -s | grep -iE '\.env|secret|key|cert|token'"
  ```

### Step 5: Stage Intended Files
- Stage only modified files corresponding to this logical change:
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && git add <files>"
  ```

### Step 6: Create Descriptive Git Commit
- Follow Conventional Commits format (`feat`, `fix`, `test`, `refactor`, `docs`, `chore`):
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && git commit -m '<type>(<scope>): <summary>'"
  ```

### Step 7: Push to GitHub Remote
- Push immediately to GitHub upstream branch:
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && git push origin master"
  ```

### Step 8: Verify Remote Commit SHA
- Confirm remote commit matches HEAD:
  ```bash
  ssh -i ~/.ssh/proximity_debian kingp@192.168.86.189 "cd /home/kingp/proximity-backend && git rev-parse HEAD && git rev-parse origin/master"
  ```
- Verify both local HEAD and `origin/master` point to the identical SHA.

---

## Visible Synchronization Ledger Format

In all responses involving backend code modifications, maintain this ledger:

```markdown
### GitHub Synchronization Ledger
- **Change:** <Description of logical change>
- **Implementation completed:** YES
- **Tests passed:** YES (<N> tests passed against proximity_test)
- **Security & diff review passed:** YES (no credentials or unintended files)
- **Commit SHA:** `<commit_sha>`
- **Push result:** SUCCESS (pushed to origin/master)
- **Remote SHA verified:** `<remote_sha>` (matches local HEAD)
- **GitHub Sync Status:** SYNCHRONIZED
```

---

## Troubleshooting & Recovery
1. **SSH Connection Failure to Debian:** Verify host IP (`192.168.86.189`) and key (`~/.ssh/proximity_debian`).
2. **GitHub Authentication Rejection:** Verify SSH key on Debian (`~/.ssh/id_ed25519`) is configured as deploy key or personal key on GitHub, and `~/.ssh/config` maps `github.com` to `~/.ssh/id_ed25519`.
3. **Remote Push Divergence:** Check `git status` and `git fetch origin`. Never force-push or rewrite public history. Rebase or merge cleanly if needed.
4. **Test Database Contamination:** Ensure `DB_NAME=proximity_test` is strictly enforced by test fixtures. Never target `proximity`.
