#!/usr/bin/env bash
#
# Fast-forward the primary checkout, which sits on `production`, to a released
# commit, and refuse while anything is uncommitted in it.
#
#   scripts/ff-primary.sh [--remote NAME] [<hash>]
#   scripts/ff-primary.sh --self-test
#
# WHY THIS IS A SCRIPT AND NOT A LINE
#
#   - **What runs from the primary runs the release.** The lane watch and the
#     weekly compute sweep are started from that checkout, so a release reaches
#     them only once the primary is fast-forwarded. The step is easy to skip
#     because the push to `production` succeeded without it.
#   - **Whatever is uncommitted there is somebody's**, left by another process
#     or session. Fast-forwarding over it discards that work with no reflog
#     entry naming it, so the status is read with `--untracked-files=all` and
#     anything at all refuses. **Find the owner.**
#   - **The server's value is read with `git ls-remote` in this run.** A
#     remote-tracking ref is whatever the last `git fetch` wrote, and merging to
#     `origin/production` moves the primary to a value nobody read. That commit is
#     fetched when this repository does not hold it: a merge made on the server
#     is here only after a fetch.
#   - **Read back afterwards.** A merge that printed success and a primary that
#     did not move are different things.
#
# The primary is found by asking which worktree holds `production`, not by a path: a
# checkout's location is a fact about this host and moves, and git already knows.
#
# Exit 0 when the primary is at the hash. Exit 9 when it refuses to start, which
# is every check above -- a refusal is never a partial move.
#
# `--self-test` builds a throwaway repository and a bare origin under $TMPDIR and
# drives every refusal, because a refusal nobody can re-run is a claim rather
# than a guarantee. **It pushes nothing**: the origin is made by clone and moved
# with `update-ref`, so the test neither needs the push door open nor teaches
# anyone to work around it.

set -u

self_test() {
  local self tmp tip1 tip2 fails=0
  self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/ff-primary-self-test.XXXXXX")

  check() { # <label> <expected exit> <phrase the output must carry>
    local label=$1 want=$2 phrase=$3 out status
    out=$(bash "$self" "${@:4}" 2>&1)
    status=$?
    if [ "$status" != "$want" ]; then
      echo "  FAIL $label: exit $status, expected $want"
      fails=$((fails + 1))
      return
    fi
    case "$out" in
      *"$phrase"*) echo "  ok   $label (exit $status)" ;;
      *)
        echo "  FAIL $label: exit $want but the message never said \"$phrase\":"
        echo "$out" | sed 's/^/         /'
        fails=$((fails + 1))
        ;;
    esac
  }

  (
    cd "$tmp" || exit 1
    git init -q work && cd work || exit 1
    git config user.email self@test && git config user.name self-test
    echo a >f && git add -A && git commit -qm one && git branch -M production
    tip1=$(git rev-parse HEAD)
    echo b >>f && git commit -qam two && tip2=$(git rev-parse HEAD)
    git clone -q --bare . ../origin.git && git remote add origin ../origin.git
    git -C ../origin.git update-ref refs/heads/production "$tip2"

    git reset -q --hard "$tip1"
    check "moves a primary that is behind" 0 "->"
    check "says so when already there" 0 "already at"

    git reset -q --hard "$tip1"
    echo "somebody else's memory" >.unowned.md
    check "refuses an untracked file" 9 "Find the owner"
    rm -f .unowned.md

    check "refuses a hash the server lacks" 9 "Publish first" "$tip1"

    git switch -q --detach HEAD
    check "refuses with no primary" 9 "no worktree holds production"
    git switch -q production

    # A merge made on the server is in no local repository until it is fetched.
    git reset -q --hard "$tip2"
    git clone -q ../origin.git ../other
    (cd ../other && git config user.email self@test && git config user.name self-test &&
      echo c >>f && git commit -qam three) || exit 1
    git -C ../origin.git fetch -q ../other +production:production || exit 1
    check "fetches a commit made on the server" 0 "->"

    git -C ../origin.git update-ref refs/heads/production "$tip1"
    git reset -q --hard "$tip2"
    check "refuses a sideways move" 9 "would not be a fast-forward"

    exit "$fails"
  )
  fails=$?
  rm -rf "$tmp"
  if [ "$fails" = 0 ]; then
    echo "ff-primary --self-test: every case held"
    return 0
  fi
  echo "ff-primary --self-test: $fails case(s) failed"
  return 1
}

if [ "${1-}" = "--self-test" ]; then
  self_test
  exit $?
fi

REFUSED=9

die() {
  echo "ff-primary: $*" >&2
  exit "$REFUSED"
}

remote=origin
want=
while [ $# -gt 0 ]; do
  case "$1" in
    --remote)
      remote=${2-}
      [ -n "$remote" ] || die "--remote needs a name"
      shift 2
      ;;
    -*) die "unknown argument: $1" ;;
    *)
      [ -z "$want" ] || die "give at most one hash"
      want=$1
      shift
      ;;
  esac
done

git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository"

# Which worktree holds production. `git worktree list --porcelain` prints a `branch`
# line only for a worktree on a branch, so a detached one cannot match by
# accident.
primary=$(git worktree list --porcelain | awk '
    /^worktree /   { path = substr($0, 10) }
    /^branch /     { if ($2 == "refs/heads/production") { print path; exit } }
')
[ -n "$primary" ] || die "no worktree holds production, so there is no primary to move"

# The server's value, read now rather than from a remote-tracking ref.
server=$(git ls-remote "$remote" refs/heads/production | cut -f1)
[ -n "$server" ] || die "$remote has no refs/heads/production"

if [ -n "$want" ]; then
  want=$(git rev-parse "$want^{commit}" 2>/dev/null) || die "cannot resolve the hash you named"
  [ "$want" = "$server" ] || die "the hash you named is not what $remote holds for production:
  you named  $want
  the server $server
Publish first -- the primary is never moved to a commit nobody else can fetch."
else
  want=$server
fi

# The server's commit is fetched when this repository does not hold it: a merge
# made on the server is here only after a fetch, and the ancestry test below
# fails on an absent commit exactly as it does on a sideways move.
if ! git cat-file -e "$want^{commit}" 2>/dev/null; then
  git fetch -q "$remote" refs/heads/production || die "cannot fetch production from $remote, and this repository does not hold $want"
  git cat-file -e "$want^{commit}" 2>/dev/null || die "$remote holds $want for production and the fetch did not bring it"
fi

here=$(git -C "$primary" rev-parse HEAD) || die "cannot read the primary's HEAD"

if [ "$here" = "$want" ]; then
  echo "ff-primary: $primary is already at ${want}"
  exit 0
fi

# -uall lists each untracked file rather than collapsing a directory into one
# line, so the refusal names each file whose owner is to be found.
dirty=$(git -C "$primary" status --porcelain --untracked-files=all)
[ -z "$dirty" ] || die "the primary has uncommitted work and fast-forwarding would discard it:
$dirty
Find the owner: a file left in the primary is somebody's work far more often
than it is litter."

git merge-base --is-ancestor "$here" "$want" || die "this would not be a fast-forward:
  primary at $here
  target    $want
The primary is never moved sideways; the target must contain what it already has."

git -C "$primary" merge --ff-only "$want" >/dev/null || die "the fast-forward itself failed"

now=$(git -C "$primary" rev-parse HEAD) || die "cannot re-read the primary's HEAD"
[ "$now" = "$want" ] || die "the merge reported success and the primary is at $now, not $want"

echo "ff-primary: $primary  ${here:0:7} -> ${now:0:7}"
