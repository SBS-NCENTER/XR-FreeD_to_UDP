#!/usr/bin/env bash
#
# Publish one commit to one branch on the remote, and ask the server afterwards
# whether it holds it.
#
#   scripts/publish.sh [--worktree DIR] [--rebased] [--remote NAME] <rev> <branch>
#   scripts/publish.sh --delete <branch> [--remote NAME]
#   scripts/publish.sh --tag <vMAJOR.MINOR.PATCH> [--remote NAME]
#   scripts/publish.sh --self-test
#
# WHAT IT GUARANTEES, AND WHY EACH IS HERE
#
#   - The commit pushed is the one resolved at the start, printed in full.
#     `<rev>` is resolved once to a hash and every later step uses that hash, so
#     a HEAD that moves during the run cannot change what is published.
#   - With --worktree, that worktree's HEAD must BE the hash and its status must
#     be empty. A hash verified at one moment and a different HEAD pushed a
#     minute later is the failure this exists for, and so is publishing a tree
#     whose uncommitted edits the reader will assume are included. `<rev>` of
#     HEAD implies --worktree of the current directory.
#   - With --worktree and --gated, those two are replaced. The hash must be
#     REACHABLE from that worktree's HEAD, and a gate record found for it must
#     be green; with no record, reachability alone is asked, and the line
#     says so. This is an added mode: without --gated nothing
#     about the form above changes, and --gated is refused for `<rev>` of HEAD,
#     where *what I have now* makes a dirty tree a real ambiguity rather than a
#     stale proxy.
#
#     Why those two are the right questions. A commit is content-addressed, so
#     pushing a hash publishes that object however the worktree has moved and
#     whatever sits uncommitted beside it; neither original condition protects
#     the push. What they protect is the inference that the hash is still what
#     the lane meant, and both are proxies for it. Reachability asks it
#     directly -- not *is it still the tip* but *is it still in this lane's
#     history* -- so it passes when a lane commits forward, which is the case
#     that closed the window, and refuses when an amend or rebase orphans the
#     hash, which is the only case on record that loses work. The gate record is
#     stronger evidence than a clean tree read minutes later: run-gates.sh
#     refuses unless HEAD is the hash and the tree is clean, and writes its
#     record only after the last verdict, so the record's existence proves the
#     conjunction held at the moment it was true.
#
#     THE RECORD'S `worktree` ROW IS INFORMATIONAL AND MUST STAY THAT WAY.
#     A record is keyed by hash alone, so a second run of the same hash from a
#     different worktree overwrites the first -- which is what happens when a
#     check is run again and lands on the other runner, each checking out in a
#     directory of its own. Requiring
#     the row to match the worktree being published from would therefore refuse
#     the documented case, and refuse it intermittently rather than always.
#     Since the hash determines the tree, any worktree's green run answers the
#     question this mode asks.
#
#     IT IS A DISCIPLINE CHECK AND NOT A SECURITY BOUNDARY. The record root is
#     writable by this user, so any session could create one by hand, and
#     HARNESS_GATES_ROOT overrides it for the self-test. Neither is a weakness this
#     mode introduces; both are stated so nobody reads the check as more than it
#     is.
#
#     A MISSING RECORD DECIDES NOTHING. The records live on a disk that can
#     come off, under the root `scripts/gates-root.sh` names, so a hash with
#     no record there is said on one line naming the hash and that root, and
#     publishes on reachability alone: the absence is on the screen rather
#     than read as a stamp. A record that is there and says no, or that
#     cannot be read, still refuses.
#   - The server's current value is read with `git ls-remote` in this run, never
#     taken from a remote-tracking ref, which `git fetch` silently rewrites.
#   - A plain publish requires the server's value to be an ancestor of the hash:
#     nothing on the server is overwritten, and a refusal names both hashes.
#   - --rebased re-publishes a branch whose history was rewritten. It fetches the
#     server's value, requires `git cherry` to show every commit there already
#     applied in the hash, and pushes with a lease pinned to that exact value --
#     so a server that moved between the read and the push refuses the push. A
#     `git cherry` that fails stops the run as unable to check (exit 2), never
#     an empty answer.
#   - `production` and `main` are never force-pushed and never deleted, and a publish
#     to `production` must fast-forward.
#   - Every move of `production` keeps `production`'s tip as the hash's first parent: a
#     release merges `main` into it and a hotfix merges into it, each one merge
#     commit, so the graph draws `main` merging into `production` at each release. A
#     fast-forward that jumps `production` along `main` is refused.
#   - `production` accepts a hash whose newest gate record ran a reach-scoped test
#     set (operator, 2026-09-24). run-gates.sh tests the packages a change can
#     reach and writes that as the record's `scope` row: `full`, `scoped`, or
#     `none` when no test gate ran; a package's tests need to run only where
#     its inputs changed since they last ran green, and `production`'s own records
#     keep that chain. A value this script does not know is refused. The deploy
#     check, `deploy/stages/1-check.sh`, builds the release commit fresh on the
#     compute host and replays the Pi's tests on the Pi; no publish requires
#     `--full`. A record from before the row existed passes and says so. The record's verdict is said beside its scope, and a
#     verdict other than `pass` refuses. A hash with no record is said,
#     naming the root looked under, and publishes: this check reads a record
#     and does not require one.
#   - A record whose cargo gates were reused from another run's (its `reused`
#     rows, and a `reused-record` row naming that run's summary) is read with
#     the run it names: `production` moves only when that run has the same
#     `cargo-key` and holds a green `gate` row for every gate reused. Its own
#     verdict may be red on a check outside cargo, which changes nothing about
#     what the cargo gates read. A run that is not there any more is said, the
#     reuse with it unverified, and `production` moves.
#   - After the push, `git ls-remote` must show the hash. A push that printed
#     success and a server that does not hold the commit are different things.
#   - --delete refuses a branch the server does not have, instead of printing
#     "- [deleted]" for a ref that never existed, deletes under a lease pinned to
#     the value just read, and checks the ref is gone.
#   - --delete refuses a branch holding a commit that the server's `main` (or
#     `production` where there is no `main`), read in the same run, carries neither by
#     content nor by tree. `git cherry` compares patches, so a branch that
#     landed through a rebase or a pick deletes; a squash merge's commit holds
#     the branch's tree, so a branch that landed squashed deletes too; one whose
#     content a rebase changed reads as unlanded and is refused with its commits
#     named, for a person to judge. Deleting the last copy of a commit prints success
#     and leaves it reachable only from a reflog, which is why this asks first.
#
# Every variable is braced in every refspec, because this repository's Bash
# tool runs zsh and `$H:refs` there is `$H` with its extension removed. The
# script itself runs under bash; the bracing is for whoever copies a line.
#
# IF THIS SCRIPT IS BROKEN
#
# A hook may refuse a raw `git push` and point here. If this script is what is
# broken, report it to the operator, whose switch is `disableAllHooks` in
# .claude/settings.local.json. Do not route around the refusal.
#
# EXIT STATUS
#
#   0  published, or deleted, and the server confirms it
#   1  refused, or the server does not confirm it: the reason is printed
#   2  cannot run: a bad argument, not a git repository, or the remote unreadable
#
# FORCED BRANCHES
#
# `--self-test` builds a throwaway bare repository and a clone under $TMPDIR and
# drives every branch against them, printing PASS or FAIL per branch; it exits 0
# only when all pass. It runs no command against any real remote. The branches:
#
#   a new branch published, and the server read back
#   a moved HEAD: --worktree HEAD is not the named hash         refused
#   a dirty tree                                                refused
#   a server value that is not an ancestor, plain publish       refused, both hashes named
#   a rebased branch re-published with a pinned lease           published
#   a --rebased publish whose git cherry fails                  cannot run, nothing pushed
#   a lease mismatch: the server moves between read and push    refused, server moved
#   a push the server rejects                                   refused, server value named
#   a push that "succeeds" and a server that does not hold it   refused, server value named
#   --rebased toward production                                       refused before any push
#   a non-fast-forward toward production                              refused
#   production toward a release merge whose first parent is production      published
#   production toward a jump along main past production's tip                refused, first parent named
#   --gated toward a hash with no gate record                   published, the root named
#   production toward a hash whose record ran a scoped test set       published, verdict and scope said
#   production toward a hash whose record ran the full set            published
#   production toward a hash whose record predates the scope row      published, said so
#   production toward a hash whose record reads verdict fail          refused, the verdict named
#   production or --gated toward a record that cannot be opened       refused, the record named
#   production toward a record cut short before its hash row          refused, the record named
#   production toward a record reused from a scoped run               published
#   production toward a record whose reused run has that gate red     refused, that run named
#   production toward a record reused from a full run                 published
#   production toward a record whose reused run has another key       refused
#   production toward a record whose reused run is not there          published, the reuse unverified
#   production toward a hash with no gate record                      published, the root named
#   --delete of a branch that never existed                     refused as absent
#   --delete of a branch holding a commit production does not carry   refused, branch kept
#   --delete of a branch production carries by content, not by hash   deleted, server read back
#   --delete of production                                            refused
#   --rebased toward main                                        refused before any push
#   --delete of main                                             refused
#   --delete of a branch main carries by tree, after a squash merge  deleted
#   --delete of a branch main carries neither by content nor by tree  refused, branch kept
#   --tag publishes an annotated version tag on production            published, and found published again
#   --tag refuses a version the server holds at another object  refused
#   --tag refuses a lightweight tag or a name not a version     refused
#   --tag refuses a commit that is not on the server's production     refused
#
# The self-test moves the server between read and push through
# PUBLISH_SELF_TEST_BEFORE_PUSH, a command run just before the push. It is
# honoured only when PUBLISH_SELF_TEST=1, which only --self-test sets.

set -euo pipefail

say() { printf 'publish: %s\n' "$*"; }
refuse() {
  printf 'publish: REFUSED: %s\n' "$*" >&2
  exit 1
}
cannot() {
  printf 'publish: cannot run: %s\n' "$*" >&2
  exit 2
}

server_value() {
  # prints the hash the server holds for refs/heads/$2, or nothing; exit 2 if unreadable
  local out
  if ! out=$(git ls-remote "$1" "refs/heads/$2" 2>&1); then
    cannot "git ls-remote $1 failed: $out"
  fi
  printf '%s' "$out" | awk -v ref="refs/heads/$2" '$2 == ref { print $1 }'
}

# Where run-gates.sh leaves its records: `scripts/gates-root.sh` names the
# root, and HARNESS_GATES_ROOT, which the self-test sets, overrides it there.
GATES_ROOT=$("$(dirname "$0")/gates-root.sh")

# **A record is found by its content, not by its path.** run-gates.sh names its
# directory after the hash it was GIVEN, which is the short form in practice,
# while this script resolves a full one -- so matching on the directory name
# would make the mode depend on how a lane happened to type an argument. The
# `hash` row inside carries the full hash and that is the thing to match.
#
# **A record that is there and cannot be read is not absent.** One that
# cannot be opened, or has no `hash` row, cannot say whose it is, so for it
# alone the directory's name, less a re-gate's suffix, is read as a prefix of
# the hash; one that names the hash and has no `verdict` row is cut short.
# Either returns 2 with why, and the caller refuses.
#
# **More than one directory can carry the same `hash` row now.** A re-gate of
# one hash preserves the earlier record under a timestamped suffix rather
# than overwriting it, so the newest run's own `summary.tsv` mtime -- not glob
# order -- decides which one this returns. Glob order was checked rather than
# assumed: `gates-*/summary.tsv` on this host does not sort the same as
# `gates-*` alone once a directory wildcard and a literal path component are
# combined, so the bare (newest) path is not reliably first and the mtime
# comparison below is load-bearing, not defensive.
named_for() { # <record> <hash>: whether the record's directory is named for the hash
  local name
  name=$(basename "$(dirname "$1")")
  name=${name%%-*}
  [ "${#name}" -ge 7 ] || return 1
  case "$2" in "${name}"*) return 0 ;; esac
  return 1
}
gate_record() {
  local want=$1 f best="" best_mtime=-1 mtime have
  for f in "${GATES_ROOT}"/gates/*/summary.tsv; do
    [ -e "${f}" ] || continue
    if [ ! -r "${f}" ]; then
      if named_for "${f}" "${want}"; then
        printf '%s could not be read' "${f}"
        return 2
      fi
      continue
    fi
    have=$(awk -F'\t' '$1 == "hash" { print $2; exit }' "${f}")
    if [ -z "${have}" ]; then
      if named_for "${f}" "${want}"; then
        printf '%s could not be read: it has no hash row' "${f}"
        return 2
      fi
      continue
    fi
    [ "${have}" = "${want}" ] || continue
    if ! grep -q "^verdict$(printf '\t')" "${f}"; then
      printf '%s could not be read: it has no verdict row' "${f}"
      return 2
    fi
    mtime=$(stat -c %Y "${f}" 2>/dev/null || echo 0)
    if [ "${mtime}" -gt "${best_mtime}" ]; then
      best=${f}
      best_mtime=${mtime}
    fi
  done
  [ -n "${best}" ] || return 1
  printf '%s' "${best}"
  return 0
}

publish() {
  local worktree="" rebased=0 gated=0 remote="origin" rev="" branch=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --worktree)
        worktree=${2-}
        shift 2
        ;;
      --rebased)
        rebased=1
        shift
        ;;
      --gated)
        gated=1
        shift
        ;;
      --remote)
        remote=${2-}
        shift 2
        ;;
      -*) cannot "unknown option $1" ;;
      *)
        if [ -z "$rev" ]; then rev=$1; elif [ -z "$branch" ]; then branch=$1; else cannot "unexpected argument $1"; fi
        shift
        ;;
    esac
  done
  [ -n "$rev" ] && [ -n "$branch" ] || cannot "usage: publish.sh [--worktree DIR] [--rebased] [--gated] [--remote NAME] <rev> <branch>"
  git rev-parse --git-dir >/dev/null 2>&1 || cannot "not inside a git repository"
  [ "$rev" = "HEAD" ] && [ -z "$worktree" ] && worktree=.
  if [ "${gated}" -eq 1 ]; then
    [ -n "$worktree" ] || cannot "--gated needs --worktree DIR: reachability is a question about one lane's history"
    [ "$rev" != "HEAD" ] || cannot "--gated names a hash that was gated; for HEAD the tree's own state is the question"
  fi

  local hash
  hash=$(git rev-parse --verify --quiet "${rev}^{commit}") || refuse "$rev does not name a commit"
  say "publishing ${hash} ($(git log -1 --format=%s "${hash}"))"

  if [ -n "$worktree" ]; then
    local head status
    head=$(git -C "${worktree}" rev-parse HEAD) || cannot "$worktree is not a git worktree"
    if [ "${gated}" -eq 0 ]; then
      [ "${head}" = "${hash}" ] || refuse "the worktree's HEAD is ${head}, not ${hash}: HEAD moved, or the wrong hash was named"
      status=$(git -C "${worktree}" status --porcelain)
      [ -z "${status}" ] || refuse "the worktree ${worktree} has uncommitted changes:"$'\n'"${status}"
    else
      git -C "${worktree}" merge-base --is-ancestor "${hash}" HEAD 2>/dev/null ||
        refuse "${hash} is not reachable from ${worktree}'s HEAD (${head}): an amend or a rebase has orphaned it"
      local record record_rc=0
      record=$(gate_record "${hash}") || record_rc=$?
      [ "${record_rc}" -ne 2 ] || refuse "a gate record of ${hash} is there and ${record}"
      if [ "${record_rc}" -eq 0 ]; then
        local verdict rows bad
        verdict=$(awk -F'\t' '$1 == "verdict" { print $2; exit }' "${record}")
        [ "${verdict}" = "pass" ] ||
          refuse "the gate record ${record} says verdict=${verdict:-<none>}"
        rows=$(awk -F'\t' '$1 == "gate" { n++ } END { print n + 0 }' "${record}")
        [ "${rows}" -gt 0 ] ||
          refuse "the gate record ${record} names no gate, so it is not evidence that anything ran"
        # A gate the change did not reach reads `unreached` and is not a
        # failure: its inputs were untouched since it last ran green.
        bad=$(awk -F'\t' '$1 == "gate" && $3 != 0 && $3 != "unreached" { printf "%s ", $2 }' "${record}")
        [ -z "${bad}" ] ||
          refuse "the gate record ${record} says verdict=pass and these did not: ${bad}"
        say "gate record ${record}: ${rows} gate(s), verdict ${verdict}"
      else
        say "no gate record for ${hash} under ${GATES_ROOT}: publishing on reachability alone"
      fi
    fi
  fi

  if [ "${rebased}" -eq 1 ]; then
    case "${branch}" in
      production | main) refuse "${branch} is never force-pushed; --rebased toward ${branch} is not a publish" ;;
    esac
  fi

  if [ "${branch}" = "production" ]; then
    local production_record scope production_verdict main_rc=0
    production_record=$(gate_record "${hash}") || main_rc=$?
    [ "${main_rc}" -ne 2 ] || refuse "a gate record of ${hash} is there and ${production_record}"
    if [ "${main_rc}" -eq 0 ]; then
      scope=$(awk -F'\t' '$1 == "scope" { print $2; exit }' "${production_record}")
      production_verdict=$(awk -F'\t' '$1 == "verdict" { print $2; exit }' "${production_record}")
      case "${scope}" in
        full | scoped | none | "") : ;;
        *) refuse "the gate record ${production_record} says scope=${scope}, which is not a value this script knows: production moves only to a main commit whose check is green; read the check of ${hash} (gh run list --branch main) and fix it on main" ;;
      esac
      [ "${production_verdict}" = "pass" ] ||
        refuse "the gate record ${production_record} says verdict=${production_verdict:-<none>}: production moves only to a main commit whose check is green; read the check of ${hash} (gh run list --branch main) and fix it on main"
      say "gate record ${production_record}: verdict ${production_verdict}, scope ${scope:-full, written before the scope row existed}"
      if grep -q "^reused$(printf '\t')" "${production_record}"; then
        local src key src_key red_row
        src=$(awk -F'\t' '$1 == "reused-record" { print $2; exit }' "${production_record}")
        if [ -z "${src}" ] || [ ! -f "${src}" ]; then
          say "the gate record ${production_record} reused its cargo gates from ${src:-a record it does not name}, which is not there: the reuse is unverified"
        else
          key=$(awk -F'\t' '$1 == "cargo-key" { print $2; exit }' "${production_record}")
          src_key=$(awk -F'\t' '$1 == "cargo-key" { print $2; exit }' "${src}")
          [ -n "${key}" ] && [ "${src_key}" = "${key}" ] ||
            refuse "${production_record} reused its cargo gates from ${src}, whose key differs: run the check of ${hash} again on the runner (gh run rerun)"
          # Every gate the record reused must have run green in the run it names.
          # `($2 in want)`, never `want[$2]`: naming an element in an awk
          # expression creates it, and a wanted set that grows with every gate
          # row it reads reports every gate of the source absent.
          red_row=$(awk -F'\t' 'NR == FNR { if ($1 == "reused") want[$2] = 1; next }
                                 $1 == "gate" && ($2 in want) { seen[$2] = 1; if ($3 != "0") bad = bad " " $2 "=" $3 }
                                 END { for (g in want) if (!seen[g]) bad = bad " " g "=absent"; print bad }' \
            "${production_record}" "${src}")
          [ -z "${red_row}" ] ||
            refuse "${production_record} reused its cargo gates from ${src}, which reads${red_row}: run the check of ${hash} again on the runner (gh run rerun)"
          say "cargo gates reused from ${src}: every reused gate green there, the same key"
        fi
      fi
    else
      say "no gate record for ${hash} under ${GATES_ROOT}: publishing without one"
    fi
  fi

  local server
  server=$(server_value "${remote}" "${branch}")
  say "server refs/heads/${branch} = ${server:-absent}"

  local lease=()
  if [ -n "${server}" ] && [ "${server}" != "${hash}" ]; then
    if [ "${rebased}" -eq 0 ]; then
      git cat-file -e "${server}^{commit}" 2>/dev/null || git fetch --quiet "${remote}" "refs/heads/${branch}" ||
        cannot "could not fetch refs/heads/${branch} from ${remote}"
      git merge-base --is-ancestor "${server}" "${hash}" ||
        refuse "the server's ${branch} is ${server}, which is not an ancestor of ${hash}; nothing on the server is overwritten by a plain publish"
      if [ "${branch}" = production ]; then
        local first
        first=$(git rev-parse --verify --quiet "${hash}^1") || first=
        [ "${first}" = "${server}" ] ||
          refuse "production moves by a merge onto its tip: ${hash}'s first parent is ${first:-none}, not production's ${server}; merge main into production with --no-ff and publish the merge"
      fi
    else
      git fetch --quiet "${remote}" "refs/heads/${branch}" || cannot "could not fetch refs/heads/${branch} from ${remote}"
      local fetched
      fetched=$(git rev-parse FETCH_HEAD)
      [ "${fetched}" = "${server}" ] || refuse "the server's ${branch} moved from ${server} to ${fetched} while it was being read"
      # cherry's own exit is read before grep sees its output: under pipefail a
      # failed cherry piped into `grep || true` reads as nothing unapplied, and
      # the lease below would then overwrite commits nobody checked.
      local cherry unapplied
      cherry=$(git cherry "${hash}" "${server}" 2>&1) ||
        cannot "git cherry ${hash} ${server} failed: ${cherry}"
      unapplied=$(printf '%s\n' "${cherry}" | grep '^+' || true)
      [ -z "${unapplied}" ] || refuse "the server's ${branch} holds commits ${hash} does not apply:"$'\n'"${unapplied}"
      lease=("--force-with-lease=refs/heads/${branch}:${server}")
    fi
  fi

  # Counted against main, where a branch is cut from and lands, and against
  # production as well for a publish to production or where there is no main.
  local against against_hash main_hash
  main_hash=$(server_value "${remote}" main)
  for against in main production; do
    if [ "${against}" = main ]; then
      against_hash=${main_hash}
    elif [ "${branch}" = production ] || [ -z "${main_hash}" ]; then
      against_hash=$(server_value "${remote}" production)
    else
      continue
    fi
    if [ -n "${against_hash}" ] && git cat-file -e "${against_hash}^{commit}" 2>/dev/null; then
      say "left-right against the server's ${against}: $(git rev-list --left-right --count "${against_hash}...${hash}" | tr '\t' ' ')"
    fi
  done

  if [ "${PUBLISH_SELF_TEST:-0}" = "1" ] && [ -n "${PUBLISH_SELF_TEST_BEFORE_PUSH:-}" ]; then
    eval "${PUBLISH_SELF_TEST_BEFORE_PUSH}"
  fi

  local push_out push_rc=0
  push_out=$(git push ${lease[@]+"${lease[@]}"} "${remote}" "${hash}:refs/heads/${branch}" 2>&1) || push_rc=$?
  printf '%s\n' "${push_out}" | sed 's/^/  git: /'

  local now
  now=$(server_value "${remote}" "${branch}")
  if [ "${push_rc}" -ne 0 ]; then
    if printf '%s' "${push_out}" | grep -q 'stale info'; then
      refuse "the server's ${branch} moved after it was read; it now holds ${now:-nothing}"
    fi
    refuse "git push exited ${push_rc}; the server's ${branch} holds ${now:-nothing}"
  fi
  [ "${now}" = "${hash}" ] || refuse "git push reported success but the server's ${branch} holds ${now:-nothing}, not ${hash}"
  say "server refs/heads/${branch} = ${now}: published"
}

delete_branch() {
  local remote="origin" branch=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --remote)
        remote=${2-}
        shift 2
        ;;
      -*) cannot "unknown option $1" ;;
      *)
        [ -z "$branch" ] || cannot "unexpected argument $1"
        branch=$1
        shift
        ;;
    esac
  done
  [ -n "${branch}" ] || cannot "usage: publish.sh --delete <branch> [--remote NAME]"
  git rev-parse --git-dir >/dev/null 2>&1 || cannot "not inside a git repository"
  case "${branch}" in
    production | main) refuse "${branch} is never deleted" ;;
  esac

  local server
  server=$(server_value "${remote}" "${branch}")
  [ -n "${server}" ] || refuse "the server has no refs/heads/${branch}; nothing was deleted"
  say "server refs/heads/${branch} = ${server}"

  # Would deleting it lose work? Asked of the server's main as read now, the
  # branch every pull request lands on, or of production where there is no main; by
  # patch rather than by hash, so a branch that landed through a rebase or a
  # pick reads as present; and by tree, so a branch that landed as one squash
  # commit reads as present: it held the base before it was squashed, so the
  # squash commit's tree is its tree exactly. The lease below is pinned to the
  # same `server`, so the commits asked about are the commits deleted.
  local base base_hash
  base_hash=$(server_value "${remote}" main)
  if [ -n "${base_hash}" ]; then base=main; else
    base=production
    base_hash=$(server_value "${remote}" production)
  fi
  [ -n "${base_hash}" ] || refuse "the server has no main and no production to compare ${branch} against; nothing was deleted"
  say "server refs/heads/${base} = ${base_hash}"
  git fetch --quiet "${remote}" "refs/heads/${base}" "refs/heads/${branch}" ||
    cannot "could not fetch ${base} and ${branch} from ${remote}"
  # cherry's own exit is read before grep sees its output: under pipefail a
  # failed cherry piped into `grep || true` reads as nothing unapplied.
  local cherry unapplied
  cherry=$(git cherry -v "${base_hash}" "${server}" 2>&1) ||
    cannot "git cherry ${base_hash} ${server} failed: ${cherry}"
  unapplied=$(printf '%s\n' "${cherry}" | grep '^+' || true)
  if [ -n "${unapplied}" ]; then
    local tree fork squashed=""
    tree=$(git rev-parse "${server}^{tree}")
    fork=$(git merge-base "${base_hash}" "${server}" 2>/dev/null || true)
    if [ -n "${fork}" ]; then
      squashed=$(git rev-list "${base_hash}" --not "${fork}" | while read -r c; do
        [ "$(git rev-parse "${c}^{tree}")" = "${tree}" ] || continue
        echo "${c}"
        break
      done)
    fi
    if [ -n "${squashed}" ]; then
      say "${base} carries ${branch} whole: ${squashed} holds its tree (a squash merge)"
    else
      refuse "${branch} at ${server} holds $(printf '%s\n' "${unapplied}" | wc -l) commit(s) that ${base} at ${base_hash} does not carry by content (git cherry), and no commit of ${base} since ${fork:-their fork} holds its tree; nothing was deleted:
$(printf '%s\n' "${unapplied}" | sed 's/^/  /')"
    fi
  fi

  local out rc=0
  out=$(git push "--force-with-lease=refs/heads/${branch}:${server}" "${remote}" ":refs/heads/${branch}" 2>&1) || rc=$?
  printf '%s\n' "${out}" | sed 's/^/  git: /'
  local now
  now=$(server_value "${remote}" "${branch}")
  [ "${rc}" -eq 0 ] || refuse "git push --delete exited ${rc}; the server's ${branch} holds ${now:-nothing}"
  [ -z "${now}" ] || refuse "the delete reported success but the server still holds ${branch} at ${now}"
  say "server refs/heads/${branch} = absent: deleted"
}

# **`--tag vMAJOR.MINOR.PATCH`: a version, published once and never moved.**
# The version grammar is `deploy/stages/lib/versions.sh`'s, read through its
# `--tag-of`, which also takes the three numbers without the `v`; only an
# annotated tag of the name it prints is published, and only when its
# commit is on the server's production as read now. A tag the server already holds
# at the same object is reported and left; at another object it is refused,
# since a version names one commit for good. The server is read again after
# the push, as for a branch.
publish_tag() {
  local remote="origin" tag=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --remote)
        remote=${2-}
        shift 2
        ;;
      -*) cannot "unknown option $1" ;;
      *)
        [ -z "${tag}" ] || cannot "unexpected argument $1"
        tag=$1
        shift
        ;;
    esac
  done
  [ -n "${tag}" ] || cannot "usage: publish.sh --tag <vMAJOR.MINOR.PATCH> [--remote NAME]"
  # The three numbers, with or without the v: the tag is vMAJOR.MINOR.PATCH.
  printf '%s' "${tag#v}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || refuse "${tag} is not a version tag, vMAJOR.MINOR.PATCH"
  tag=v${tag#v}
  git rev-parse --git-dir >/dev/null 2>&1 || cannot "not inside a git repository"
  [ "$(git cat-file -t "refs/tags/${tag}" 2>/dev/null)" = tag ] || refuse "${tag} is not an annotated tag here"
  local object commit out server production_hash rc=0
  object=$(git rev-parse "refs/tags/${tag}")
  commit=$(git rev-parse "refs/tags/${tag}^{commit}")
  out=$(git ls-remote "${remote}" "refs/tags/${tag}" 2>&1) || cannot "git ls-remote ${remote} failed: ${out}"
  server=$(printf '%s\n' "${out}" | awk -v r="refs/tags/${tag}" '$2 == r { print $1 }')
  if [ -n "${server}" ]; then
    [ "${server}" = "${object}" ] || refuse "the server holds ${tag} at ${server} and here it is ${object}: a version is never moved"
    say "server refs/tags/${tag} = ${server}: already published"
    return 0
  fi
  production_hash=$(server_value "${remote}" production)
  [ -n "${production_hash}" ] || refuse "the server has no production to find ${commit} on"
  git fetch --quiet "${remote}" refs/heads/production || cannot "could not fetch production from ${remote}"
  git merge-base --is-ancestor "${commit}" "${production_hash}" || refuse "${tag}'s commit ${commit} is not on the server's production at ${production_hash}"
  out=$(git push "${remote}" "refs/tags/${tag}:refs/tags/${tag}" 2>&1) || rc=$?
  printf '%s\n' "${out}" | sed 's/^/  git: /'
  out=$(git ls-remote "${remote}" "refs/tags/${tag}" 2>&1) || cannot "git ls-remote ${remote} failed: ${out}"
  server=$(printf '%s\n' "${out}" | awk -v r="refs/tags/${tag}" '$2 == r { print $1 }')
  [ "${rc}" -eq 0 ] || refuse "git push of ${tag} exited ${rc}; the server holds ${server:-nothing}"
  [ "${server}" = "${object}" ] || refuse "the push reported success but the server holds ${tag} at ${server:-nothing}"
  say "server refs/tags/${tag} = ${server}: published, commit ${commit}"
}

self_test() {
  local self tmp failures=0
  self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/publish-self-test.XXXXXX")
  # expanded now: at EXIT this function's locals are gone, and set -u would fail
  # tmp is this function's local: expand it now, since the trap fires after the
  # function has returned.
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp}'" EXIT
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

  git init -q --bare -b production "${tmp}/srv.git"
  cat >"${tmp}/srv.git/hooks/pre-receive" <<'HOOK'
#!/bin/sh
[ -e "$(git rev-parse --git-dir)/reject" ] && { echo "rejected by test hook" >&2; exit 1; }
exit 0
HOOK
  cat >"${tmp}/srv.git/hooks/post-receive" <<'HOOK'
#!/bin/sh
d=$(git rev-parse --git-dir)
[ -e "$d/undo" ] || exit 0
while read old new ref; do git update-ref "$ref" "$old" 2>/dev/null || git update-ref -d "$ref"; done
HOOK
  chmod +x "${tmp}/srv.git/hooks/pre-receive" "${tmp}/srv.git/hooks/post-receive"

  git clone -q "${tmp}/srv.git" "${tmp}/work" 2>/dev/null
  git -C "${tmp}/work" symbolic-ref HEAD refs/heads/production
  echo one >"${tmp}/work/f"
  git -C "${tmp}/work" add f
  git -C "${tmp}/work" commit -q -m one
  git -C "${tmp}/work" push -q origin production
  git clone -q "${tmp}/srv.git" "${tmp}/other" 2>/dev/null

  check() { # check <name> <expected exit> <expected text or -> <command...>
    local name=$1 want=$2 text=$3
    shift 3
    local out rc=0
    out=$( (cd "${tmp}/work" && "$@") 2>&1) || rc=$?
    if [ "${rc}" -eq "${want}" ] && { [ "${text}" = "-" ] || printf '%s' "${out}" | grep -qF -- "${text}"; }; then
      printf 'PASS  %s\n' "${name}"
    else
      printf 'FAIL  %s  (exit %s, wanted %s)\n%s\n' "${name}" "${rc}" "${want}" "$(printf '%s' "${out}" | sed 's/^/        /')"
      failures=$((failures + 1))
    fi
  }
  srv() { git -C "${tmp}/srv.git" rev-parse --verify --quiet "refs/heads/$1" || true; }

  git -C "${tmp}/work" switch -q -c feat/a
  echo two >>"${tmp}/work/f"
  git -C "${tmp}/work" commit -q -am two
  local h2
  h2=$(git -C "${tmp}/work" rev-parse HEAD)
  check "a new branch published, and the server read back" 0 "published" bash "${self}" HEAD feat/a
  [ "$(srv feat/a)" = "${h2}" ] || {
    printf 'FAIL  server does not hold the published hash\n'
    failures=$((failures + 1))
  }

  local h1
  h1=$(git -C "${tmp}/work" rev-parse HEAD~1)
  check "a moved HEAD" 1 "HEAD moved" bash "${self}" --worktree . "${h1}" feat/a

  echo dirty >>"${tmp}/work/f"
  check "a dirty tree" 1 "uncommitted changes" bash "${self}" HEAD feat/a
  git -C "${tmp}/work" restore f

  # ---- --gated, which replaces two proxies with the questions they stood for
  export HARNESS_GATES_ROOT="${tmp}/buildroot"
  mkdir -p "${HARNESS_GATES_ROOT}"
  record() { # record <hash> <verdict> [<a second gate's exit>]
    local d="${HARNESS_GATES_ROOT}/gates/$1"
    mkdir -p "${d}"
    {
      printf 'version\t1\n'
      printf 'hash\t%s\n' "$1"
      printf 'gate\topen-work\t0\n'
      [ -z "${3-}" ] || printf 'gate\tno-hex\t%s\n' "$3"
      printf 'verdict\t%s\n' "$2"
    } >"${d}/summary.tsv"
  }

  echo three >>"${tmp}/work/f"
  git -C "${tmp}/work" commit -q -am three
  local h3
  h3=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${h3}" pass
  echo four >>"${tmp}/work/f"
  git -C "${tmp}/work" commit -q -am four
  echo dirty >>"${tmp}/work/f"

  # **The case the window closed on**: the lane committed forward and is
  # mid-edit, so both original conditions are false while the hash is still
  # exactly what the lane reported. A suite without this one cannot tell this
  # change from no change.
  check "--gated publishes an ancestor of HEAD from a dirty tree" 0 "published" \
    bash "${self}" --worktree . --gated "${h3}" feat/a
  [ "$(srv feat/a)" = "${h3}" ] ||
    {
      printf 'FAIL  server does not hold the gated hash\n'
      failures=$((failures + 1))
    }
  git -C "${tmp}/work" restore f

  local h4
  h4=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${h4}" pass
  git -C "${tmp}/work" commit -q --amend -m "four, amended"
  check "--gated refuses a hash an amend orphaned" 1 "not reachable" \
    bash "${self}" --worktree . --gated "${h4}" feat/a

  local h5
  h5=$(git -C "${tmp}/work" rev-parse HEAD)
  check "--gated publishes a hash with no record and names the root it looked under" 0 \
    "no gate record for ${h5} under ${HARNESS_GATES_ROOT}: publishing on reachability alone" \
    bash "${self}" --worktree . --gated "${h5}" feat/no-record
  [ "$(srv feat/no-record)" = "${h5}" ] ||
    {
      printf 'FAIL  server does not hold the hash published with no record\n'
      failures=$((failures + 1))
    }
  record "${h5}" fail
  check "--gated refuses a record whose verdict is fail" 1 "verdict=fail" \
    bash "${self}" --worktree . --gated "${h5}" feat/a
  record "${h5}" pass 1
  check "--gated refuses a green verdict sitting over a failed gate" 1 "these did not" \
    bash "${self}" --worktree . --gated "${h5}" feat/a
  record "${h5}" pass unreached
  check "--gated accepts a gate the change did not reach" 0 "published" \
    bash "${self}" --worktree . --gated "${h5}" feat/unreached
  record "${h5}" pass
  check "--gated needs a worktree to ask reachability of" 2 "needs --worktree" \
    bash "${self}" --gated "${h5}" feat/a
  check "--gated is refused for HEAD, where the tree is the question" 2 "for HEAD the tree" \
    bash "${self}" --worktree . --gated HEAD feat/a

  # **Two records for one hash, the shape a re-gate now produces**: an older,
  # failing one preserved under a timestamped-suffix directory, and a newer,
  # passing one at the bare path. `gate_record()` must return the newer one
  # by its `summary.tsv` mtime -- glob order alone was shown not to, above.
  echo eight >>"${tmp}/work/f"
  git -C "${tmp}/work" commit -q -am eight
  local h8
  h8=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${h8}" fail
  mv "${HARNESS_GATES_ROOT}/gates/${h8}" "${HARNESS_GATES_ROOT}/gates/${h8}-20200101T000000Z"
  touch -d '2020-01-01' "${HARNESS_GATES_ROOT}/gates/${h8}-20200101T000000Z/summary.tsv"
  record "${h8}" pass
  check "--gated finds the newer of two records for one hash, not the older" 0 "published" \
    bash "${self}" --worktree . --gated "${h8}" feat/newest-wins
  [ "$(srv feat/newest-wins)" = "${h8}" ] ||
    {
      printf 'FAIL  server does not hold the newest-record-wins hash\n'
      failures=$((failures + 1))
    }

  # **The records above test this script's READER and cannot test that the
  # reader's vocabulary is the writer's**, because nothing here runs
  # run-gates.sh. So the coupling is asserted directly: every row name read
  # below must be one that script emits. A format change on either side turns
  # this red instead of turning the mode silently permissive.
  # The writer is the project's own; where none sits beside this script the
  # contract is stated instead of asserted.
  local writer row coupled=0
  writer=$(dirname "${self}")/run-gates.sh
  if [ -f "${writer}" ]; then
    for row in hash verdict gate; do
      if grep -qF "printf '${row}\\t" "${writer}"; then
        coupled=$((coupled + 1))
      else
        printf 'FAIL  run-gates.sh emits no %s row, which --gated reads\n' "${row}"
        failures=$((failures + 1))
      fi
    done
    [ "${coupled}" -ne 3 ] ||
      printf 'PASS  the record rows --gated reads are the rows run-gates writes\n'
  else
    printf 'PASS  no run-gates.sh beside this script; its summary.tsv must carry hash, verdict and gate rows\n'
  fi
  unset HARNESS_GATES_ROOT

  git -C "${tmp}/work" switch -q -c diverged "${h1}"
  echo three >"${tmp}/work/g"
  git -C "${tmp}/work" add g
  git -C "${tmp}/work" commit -q -m three
  check "a server value that is not an ancestor" 1 "not an ancestor" bash "${self}" HEAD feat/a

  git -C "${tmp}/other" pull -q --ff-only origin production 2>/dev/null
  echo main2 >"${tmp}/other/m"
  git -C "${tmp}/other" add m
  git -C "${tmp}/other" commit -q -m main2
  git -C "${tmp}/other" push -q origin production
  git -C "${tmp}/work" switch -q feat/a
  git -C "${tmp}/work" fetch -q origin production
  git -C "${tmp}/work" rebase -q origin/production
  check "a rebased branch re-published with a pinned lease" 0 "published" bash "${self}" --rebased HEAD feat/a

  echo four >>"${tmp}/work/f"
  git -C "${tmp}/work" commit -q -am four
  git -C "${tmp}/work" rebase -q --force-rebase HEAD~1 2>/dev/null

  # A git whose cherry fails and whose every other subcommand is the real one.
  # The fetch before cherry puts both objects here, so no ref this test could
  # name makes the real cherry fail at that point.
  local real_git
  real_git=$(command -v git)
  mkdir -p "${tmp}/failing-cherry"
  printf '#!/bin/sh\n[ "$1" = cherry ] && { echo "fatal: cherry failed on purpose" >&2; exit 128; }\nexec "%s" "$@"\n' \
    "${real_git}" >"${tmp}/failing-cherry/git"
  chmod +x "${tmp}/failing-cherry/git"
  local before_cherry
  before_cherry=$(srv feat/a)
  check "a --rebased publish whose git cherry fails" 2 "failed: fatal: cherry failed on purpose" \
    env PATH="${tmp}/failing-cherry:${PATH}" bash "${self}" --rebased HEAD feat/a
  [ "$(srv feat/a)" = "${before_cherry}" ] ||
    {
      printf 'FAIL  feat/a moved when cherry failed\n'
      failures=$((failures + 1))
    }
  check "a lease mismatch: the server moves between read and push" 1 "moved" \
    env PUBLISH_SELF_TEST=1 PUBLISH_SELF_TEST_BEFORE_PUSH="git -C '${tmp}/other' fetch -q origin feat/a && git -C '${tmp}/other' switch -q -c racer FETCH_HEAD && echo race >> '${tmp}/other/r' && git -C '${tmp}/other' add r && git -C '${tmp}/other' commit -q -m race && git -C '${tmp}/other' push -q origin racer:feat/a" \
    bash "${self}" --rebased HEAD feat/a

  git -C "${tmp}/work" switch -q -c feat/b origin/production
  echo five >"${tmp}/work/h"
  git -C "${tmp}/work" add h
  git -C "${tmp}/work" commit -q -m five
  touch "${tmp}/srv.git/reject"
  check "a push the server rejects" 1 "holds nothing" bash "${self}" HEAD feat/b
  rm -f "${tmp}/srv.git/reject"

  touch "${tmp}/srv.git/undo"
  check "a push that succeeds and a server that does not hold it" 1 "reported success" bash "${self}" HEAD feat/b
  rm -f "${tmp}/srv.git/undo"

  local production_before
  production_before=$(srv production)
  check "--rebased toward production" 1 "never force-pushed" bash "${self}" --rebased HEAD production
  [ "$(srv production)" = "${production_before}" ] || {
    printf 'FAIL  production moved during a refused publish\n'
    failures=$((failures + 1))
  }

  git -C "${tmp}/work" switch -q diverged
  check "a non-fast-forward toward production" 1 "not an ancestor" bash "${self}" HEAD production

  # ---- every move of production keeps production's tip as its first parent: a jump along
  # a branch past it is refused, the merge of that branch onto it publishes
  git -C "${tmp}/work" fetch -q origin
  git -C "${tmp}/work" switch -q -c feat/jump origin/production
  echo j1 >"${tmp}/work/j1"
  git -C "${tmp}/work" add j1
  git -C "${tmp}/work" commit -q -m j1
  echo j2 >"${tmp}/work/j2"
  git -C "${tmp}/work" add j2
  git -C "${tmp}/work" commit -q -m j2
  local before_jump
  before_jump=$(srv production)
  check "production toward a jump along main past production's tip" 1 "first parent" bash "${self}" HEAD production
  [ "$(srv production)" = "${before_jump}" ] || {
    printf 'FAIL  production moved on a jump\n'
    failures=$((failures + 1))
  }
  git -C "${tmp}/work" switch -q -c release/merge origin/production
  git -C "${tmp}/work" merge -q --no-ff -m "release: merge" feat/jump
  check "production toward a release merge whose first parent is production" 0 "published" bash "${self}" HEAD production
  [ "$(srv production)" = "$(git -C "${tmp}/work" rev-parse HEAD)" ] ||
    {
      printf 'FAIL  production did not move to the release merge\n'
      failures=$((failures + 1))
    }

  # ---- production carries a full test result, read from the record's scope row
  export HARNESS_GATES_ROOT="${tmp}/buildroot"
  git -C "${tmp}/work" switch -q -c feat/production-scope origin/production
  echo scope >"${tmp}/work/s"
  git -C "${tmp}/work" add s
  git -C "${tmp}/work" commit -q -m scope
  local hs
  hs=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${hs}" pass
  printf 'scope\tscoped\tgodo-units\n' >>"${HARNESS_GATES_ROOT}/gates/${hs}/summary.tsv"
  check "production toward a hash whose record ran a scoped test set" 0 "verdict pass, scope scoped" bash "${self}" HEAD production
  [ "$(srv production)" = "${hs}" ] || {
    printf 'FAIL  production did not move to a scoped record\n'
    failures=$((failures + 1))
  }
  echo full >"${tmp}/work/f"
  git -C "${tmp}/work" add f
  git -C "${tmp}/work" commit -q -m full
  hs=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${hs}" pass
  printf 'scope\tfull\t--full\n' >>"${HARNESS_GATES_ROOT}/gates/${hs}/summary.tsv"
  check "production toward a hash whose record ran the full set" 0 "published" bash "${self}" HEAD production
  [ "$(srv production)" = "${hs}" ] || {
    printf 'FAIL  production did not move to the full record\n'
    failures=$((failures + 1))
  }
  echo older >"${tmp}/work/o"
  git -C "${tmp}/work" add o
  git -C "${tmp}/work" commit -q -m older
  local ho
  ho=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${ho}" pass
  check "production toward a hash whose record predates the scope row" 0 "written before the scope row existed" \
    bash "${self}" HEAD production
  echo red >"${tmp}/work/rd"
  git -C "${tmp}/work" add rd
  git -C "${tmp}/work" commit -q -m red
  local hd
  hd=$(git -C "${tmp}/work" rev-parse HEAD)
  record "${hd}" fail
  production_before=$(srv production)
  check "production toward a hash whose record reads verdict fail" 1 "summary.tsv says verdict=fail" bash "${self}" HEAD production
  [ "$(srv production)" = "${production_before}" ] || {
    printf 'FAIL  production moved to a hash whose record is red\n'
    failures=$((failures + 1))
  }
  # A record that is there and cannot be read is refused, never taken for
  # absent: one that cannot be opened, named for the hash as run-gates.sh
  # names it, and one cut short before its hash row.
  rm -rf "${HARNESS_GATES_ROOT}/gates/${hd}"
  mkdir -p "${HARNESS_GATES_ROOT}/gates/${hd:0:8}"
  printf 'version\t1\nhash\t%s\nverdict\tpass\n' "${hd}" >"${HARNESS_GATES_ROOT}/gates/${hd:0:8}/summary.tsv"
  chmod 000 "${HARNESS_GATES_ROOT}/gates/${hd:0:8}/summary.tsv"
  check "production toward a hash whose record cannot be opened" 1 \
    "a gate record of ${hd} is there and ${HARNESS_GATES_ROOT}/gates/${hd:0:8}/summary.tsv could not be read" bash "${self}" HEAD production
  check "--gated toward a hash whose record cannot be opened" 1 "summary.tsv could not be read" \
    bash "${self}" --worktree . --gated "${hd}" feat/unreadable
  chmod 644 "${HARNESS_GATES_ROOT}/gates/${hd:0:8}/summary.tsv"
  printf 'version\t1\n' >"${HARNESS_GATES_ROOT}/gates/${hd:0:8}/summary.tsv"
  check "production toward a hash whose record was cut short" 1 "summary.tsv could not be read: it has no hash row" \
    bash "${self}" HEAD production
  [ "$(srv production)" = "${production_before}" ] || {
    printf 'FAIL  production moved to a hash whose record cannot be read\n'
    failures=$((failures + 1))
  }
  rm -rf "${HARNESS_GATES_ROOT}/gates/${hd:0:8}"
  # A record reused from another run's: its scope row is that run's, and the
  # run it names is read too. Each commit a case publishes to production is made on
  # production's tip, which every move of production keeps as the first parent.
  on_production() { git -C "${tmp}/work" fetch -q origin && git -C "${tmp}/work" reset -q --hard origin/production; }
  on_production
  echo reused >"${tmp}/work/r"
  git -C "${tmp}/work" add r
  git -C "${tmp}/work" commit -q -m reused
  local hr k
  hr=$(git -C "${tmp}/work" rev-parse HEAD)
  k=$(printf 'k%.0s' $(seq 64))
  source_record() { # source_record <name> <scope> <test gate exit> [<cargo key>]
    mkdir -p "${HARNESS_GATES_ROOT}/gates/$1"
    # `fmt` is a gate the source ran and the record never reused: the check
    # must leave it out of the wanted set, and a source holding only the
    # reused gate cannot show whether it does.
    printf 'hash\t%s\ncargo-key\t%s\nscope\t%s\tx\ngate\tfmt\t0\ngate\ttest\t%s\nverdict\tfail\n' "$1" "${4:-${k}}" "$2" "$3" \
      >"${HARNESS_GATES_ROOT}/gates/$1/summary.tsv"
  }
  reused_record() { # reused_record <scope row> <source name>
    record "${hr}" pass
    printf 'cargo-key\t%s\nscope\t%s\tx\nreused\ttest\t%s\t%s\nreused-record\t%s\n' "${k}" "$1" "$2" "${k}" \
      "${HARNESS_GATES_ROOT}/gates/$2/summary.tsv" >>"${HARNESS_GATES_ROOT}/gates/${hr}/summary.tsv"
  }
  # The sources read verdict=fail on purpose: a red outside cargo in the run
  # reused from changes nothing about what its cargo gates read.
  source_record src-red scoped 101
  source_record src-scoped scoped 0
  source_record src-full full 0
  production_before=$(srv production)
  reused_record scoped src-red
  check "production toward a record whose reused run has that gate red" 1 "gates/src-red/summary.tsv, which reads test=101" \
    bash "${self}" HEAD production
  [ "$(srv production)" = "${production_before}" ] || {
    printf 'FAIL  production moved to a record reused from a red gate\n'
    failures=$((failures + 1))
  }
  reused_record scoped src-scoped
  check "production toward a record reused from a scoped run" 0 "every reused gate green there" bash "${self}" HEAD production
  [ "$(srv production)" = "${hr}" ] || {
    printf 'FAIL  production did not move to the reused scoped record\n'
    failures=$((failures + 1))
  }
  on_production
  echo reused-full >"${tmp}/work/rf"
  git -C "${tmp}/work" add rf
  git -C "${tmp}/work" commit -q -m reused-full
  hr=$(git -C "${tmp}/work" rev-parse HEAD)
  reused_record full src-full
  check "production toward a record reused from a full run" 0 "cargo gates reused from" bash "${self}" HEAD production
  [ "$(srv production)" = "${hr}" ] || {
    printf 'FAIL  production did not move to the reused full record\n'
    failures=$((failures + 1))
  }
  # A run reused from that is there and keyed differently still refuses; one
  # that is not there any more is said, and production moves.
  on_production
  echo reused-other >"${tmp}/work/ro"
  git -C "${tmp}/work" add ro
  git -C "${tmp}/work" commit -q -m reused-other
  hr=$(git -C "${tmp}/work" rev-parse HEAD)
  source_record src-other scoped 0 "$(printf 'o%.0s' $(seq 64))"
  reused_record scoped src-other
  production_before=$(srv production)
  check "production toward a record whose reused run has another key" 1 "gates/src-other/summary.tsv, whose key differs" \
    bash "${self}" HEAD production
  [ "$(srv production)" = "${production_before}" ] || {
    printf 'FAIL  production moved to a record reused from another key\n'
    failures=$((failures + 1))
  }
  reused_record scoped src-gone
  check "production toward a record whose reused run is not there" 0 \
    "gates/src-gone/summary.tsv, which is not there: the reuse is unverified" bash "${self}" HEAD production
  [ "$(srv production)" = "${hr}" ] || {
    printf 'FAIL  production did not move past a reused run that is not there\n'
    failures=$((failures + 1))
  }
  on_production
  echo unrecorded >"${tmp}/work/u"
  git -C "${tmp}/work" add u
  git -C "${tmp}/work" commit -q -m unrecorded
  local hu
  hu=$(git -C "${tmp}/work" rev-parse HEAD)
  check "production toward a hash with no gate record" 0 \
    "no gate record for ${hu} under ${HARNESS_GATES_ROOT}: publishing without one" bash "${self}" HEAD production
  [ "$(srv production)" = "${hu}" ] || {
    printf 'FAIL  production did not move to a hash with no record\n'
    failures=$((failures + 1))
  }
  if [ ! -f "$(dirname "${self}")/run-gates.sh" ]; then
    printf 'PASS  no run-gates.sh beside this script; its summary.tsv must carry a scope row\n'
  elif grep -qF "printf 'scope\\t" "$(dirname "${self}")/run-gates.sh"; then
    printf 'PASS  the scope row publish.sh reads is a row run-gates writes\n'
  else
    printf 'FAIL  run-gates.sh emits no scope row, which a publish to production reads\n'
    failures=$((failures + 1))
  fi
  unset HARNESS_GATES_ROOT

  check "--delete of a branch that never existed" 1 "nothing was deleted" bash "${self}" --delete never-existed

  # feat/a on the server ends in the racer's commit, which production never took.
  local held
  held=$(srv feat/a)
  check "--delete of a branch holding a commit production does not carry" 1 "does not carry by content" \
    bash "${self}" --delete feat/a
  [ "$(srv feat/a)" = "${held}" ] ||
    {
      printf 'FAIL  feat/a moved during a refused delete\n'
      failures=$((failures + 1))
    }

  # A branch that landed under another hash: production moves first, so the pick
  # cannot reproduce the branch's commit and ancestry alone would say unlanded.
  git -C "${tmp}/work" fetch -q origin production
  git -C "${tmp}/work" switch -q -c feat/landed origin/production
  echo landed >"${tmp}/work/l"
  git -C "${tmp}/work" add l
  git -C "${tmp}/work" commit -q -m landed
  local hl
  hl=$(git -C "${tmp}/work" rev-parse HEAD)
  git -C "${tmp}/work" push -q origin feat/landed
  git -C "${tmp}/other" switch -q production
  git -C "${tmp}/other" pull -q --ff-only origin production 2>/dev/null
  echo main3 >"${tmp}/other/m3"
  git -C "${tmp}/other" add m3
  git -C "${tmp}/other" commit -q -m main3
  git -C "${tmp}/other" fetch -q origin feat/landed
  git -C "${tmp}/other" cherry-pick "${hl}" >/dev/null
  git -C "${tmp}/other" push -q origin production
  if git -C "${tmp}/srv.git" merge-base --is-ancestor "${hl}" production; then
    printf 'FAIL  feat/landed is an ancestor of production, so the next case tests ancestry\n'
    failures=$((failures + 1))
  fi
  check "--delete of a branch production carries by content, not by hash" 0 "deleted" \
    bash "${self}" --delete feat/landed
  [ -z "$(srv feat/landed)" ] ||
    {
      printf 'FAIL  feat/landed still on the server after delete\n'
      failures=$((failures + 1))
    }
  check "--delete of production" 1 "never deleted" bash "${self}" --delete production

  # ---- main: the branch pull requests land on, guarded as production is, and the
  # base every --delete is asked against once it exists; a branch squash-merged
  # into it is carried by tree, not by patch.
  git -C "${tmp}/other" switch -q production
  git -C "${tmp}/other" pull -q --ff-only origin production 2>/dev/null
  git -C "${tmp}/other" switch -q -c main
  git -C "${tmp}/other" push -q origin main
  check "--rebased toward main" 1 "never force-pushed" bash "${self}" --rebased HEAD main
  check "--delete of main" 1 "never deleted" bash "${self}" --delete main
  git -C "${tmp}/work" fetch -q origin main
  git -C "${tmp}/work" switch -q -c feat/sq origin/main
  echo sq1 >"${tmp}/work/q"
  git -C "${tmp}/work" add q
  git -C "${tmp}/work" commit -q -m sq1
  echo sq2 >>"${tmp}/work/q"
  git -C "${tmp}/work" commit -q -am sq2
  check "a branch to be squash-merged, published" 0 "published" bash "${self}" HEAD feat/sq
  git -C "${tmp}/other" fetch -q origin feat/sq
  git -C "${tmp}/other" merge -q --squash origin/feat/sq >/dev/null
  git -C "${tmp}/other" commit -q -m "sq (#1)"
  git -C "${tmp}/other" push -q origin main
  check "--delete of a branch main carries by tree, after a squash merge" 0 "a squash merge" \
    bash "${self}" --delete feat/sq
  [ -z "$(srv feat/sq)" ] ||
    {
      printf 'FAIL  feat/sq still on the server after delete\n'
      failures=$((failures + 1))
    }
  git -C "${tmp}/work" fetch -q origin main
  git -C "${tmp}/work" switch -q -c feat/sq2 origin/main
  echo sq3 >"${tmp}/work/q3"
  git -C "${tmp}/work" add q3
  git -C "${tmp}/work" commit -q -m sq3
  check "a second branch, published" 0 "published" bash "${self}" HEAD feat/sq2
  check "--delete of a branch main carries neither by content nor by tree" 1 "holds its tree" \
    bash "${self}" --delete feat/sq2
  git -C "${tmp}/work" switch -q feat/landed

  # ---- --tag
  git -C "${tmp}/work" switch -q production
  git -C "${tmp}/work" tag -a v1.0.0 -m "v1.0.0" HEAD
  check "--tag publishes an annotated version tag on production" 0 "published" bash "${self}" --tag v1.0.0
  check "--tag again finds it published" 0 "already published" bash "${self}" --tag v1.0.0
  check "--tag takes the three numbers without the v" 0 "already published" bash "${self}" --tag 1.0.0
  git -C "${tmp}/work" tag -a -f v1.0.0 -m "moved" HEAD >/dev/null
  check "--tag refuses a version the server holds at another object" 1 "never moved" bash "${self}" --tag v1.0.0
  git -C "${tmp}/work" tag v1.0.1 HEAD
  check "--tag refuses a lightweight tag" 1 "not an annotated tag" bash "${self}" --tag v1.0.1
  check "--tag refuses a name that is not a version" 1 "not a version tag" bash "${self}" --tag release-1
  git -C "${tmp}/work" switch -q -c feat/tagged
  echo off-production >>"${tmp}/work/f"
  git -C "${tmp}/work" commit -q -am off-production
  git -C "${tmp}/work" tag -a v1.1.0 -m "v1.1.0" HEAD
  check "--tag refuses a commit that is not on the server's production" 1 "not on the server's production" bash "${self}" --tag v1.1.0

  if [ "${failures}" -eq 0 ]; then
    printf 'publish self-test: every branch behaved\n'
    return 0
  fi
  printf 'publish self-test: %s failure(s)\n' "${failures}"
  return 1
}

case "${1-}" in
  --self-test) self_test ;;
  --delete)
    shift
    delete_branch "$@"
    ;;
  --tag)
    shift
    publish_tag "$@"
    ;;
  "" | -h | --help)
    sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
  *) publish "$@" ;;
esac
