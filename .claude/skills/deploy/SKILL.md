---
name: deploy
description: The seat's. Use when deploying a release merge on production to the device: the stages in order, what each changes, the tag's confirmation and the report's shape.
---

# deploy — the stages, in the order the seat runs them

The rules are `roles/seat.md`, under Deploy: only on the operator's word, of
a commit on `production`, every stage on that word, and the tag on the
operator's confirmation of its message. The version commit before a deploy
is the `integrate` skill's release. The source project's eight stages, with
their Pi-side scripts and tests, are `origin/deploy/stages/`; a project
writes its own with the same shape.

## A deploy

1. **Post the sequence in chat**, one line per stage, with the commit:

   ```
   1  deploy/stages/1-check.sh <commit>       build it fresh, replay the device's tests, seal the result
   2  deploy/stages/2-snapshot.sh <commit>    the installed build's rollback copy, the archive of what is installed
   3  deploy/stages/3-dry-run.sh <commit>     the installer's every check, nothing changed
   4  deploy/stages/4-install.sh <commit>     the files replaced, each checked against the seal
   5  deploy/stages/5-restart.sh <commit>     the units restarted in order
   6  deploy/stages/6-verify.sh <commit>      the units active and running the sealed binaries
   7  deploy/stages/7-tag.sh <version> <commit> --notes <file>
   8  deploy/stages/8-archive.sh              older copies leave the device
   ```

2. **Run them in order on the operator's word to deploy**, with no OK asked
   between them; only the tag stage waits, for the operator to confirm its
   tag's whole message. A stage that is not green ends the deploy there.
   What a stage would do is shown first by `--print` for the stages that
   touch the device and `--dry-run` for the rest; the tag stage takes
   neither.
3. **Stage 1 is the deploy's own check**, and nothing of a pull request's
   check counts for it.
4. **The tag** is shown to the operator as its whole message before it is
   made. Its notes start from the merged pull requests' *Operator-visible*
   lines, written for the operator in their language, grouped by what they
   see, with no issue number and no `D` number.
5. **Report** that deploy's version, commit, time and its own anomalies, and
   not the routine every deploy shares.

## A rollback

The previous release is the one rollback build the device keeps; a rollback
runs that build's installer without `--dry-run`, builds or checks nothing
new, then restarts and verifies the units with stages 5 and 6.
