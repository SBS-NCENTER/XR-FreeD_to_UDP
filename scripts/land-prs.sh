#!/usr/bin/env bash
#
# Land pull requests into main one after another, each on a green check of
# itself merged with main as main stands at its turn. The seat runs it after
# reading each diff whole (the `integrate` skill).
#
#   scripts/land-prs.sh --co-author '<Name> <email>' <number>@<head> ...
#
# <head> is the head the seat read, its full hash or a prefix. For each pull
# request, in the order given:
#
#   1. its head is still <head>, or it is skipped: what would land is unread
#   2. when the branch does not contain main, what main gained since the
#      branch's base decides. Code (CODE_PATHS): GitHub rebases the branch
#      onto main (updatePullRequestBranch, REBASE, pinned to that head), so the
#      check covers the two together, and the new head must be the same
#      number of commits over main with the same change by patch-id, or it is
#      skipped. Nothing but documents, rules and scripts, the seat's own
#      commits: the branch lands as it stands, its lane beside them in the
#      graph, and main's own check reads the merge. A branch cut from main's tip
#      needs neither
#   3. the check named `check` on the head is waited for and watched
#   4. on green, a merge commit pinned to the head, so the branch keeps its
#      lane in the graph: the subject is the title with `(PR #<number>)`
#      after it, the body the branch's own commits' subjects, one line each,
#      and the Co-Authored-By line given
#
# Merging one moves main, so the next one's green is taken again on the new
# main before it lands. A refused update (a conflict), a red check or a moved
# head skips that pull request and the train goes on; a merge state other
# than CLEAN or a refused merge stops it. Each outcome is one line beginning
# LANDED, SKIPPED or STOPPED; the other lines are the detail. Exit 0 when
# every one landed, 3 when one was skipped, 4 when the train stopped, 2 on a
# usage error.

set -u

usage() {
  sed -n '7p' "$0" >&2
  exit 2
}
co_author=
if [ "${1:-}" = --co-author ] && [ $# -ge 2 ]; then
  co_author=$2
  shift 2
fi
if [ -z "$co_author" ] || [ $# -eq 0 ]; then usage; fi
for arg in "$@"; do
  case $arg in [0-9]*@[0-9a-f]???????*) ;; *)
    echo "STOPPED: $arg is not <number>@<head>"
    usage
    ;;
  esac
done

now() { TZ='Asia/Seoul' date '+%H:%M:%S KST'; }
field() { gh pr view "$1" --json "$2" -q ".$2"; }

# What a check of a lane's code reads: a change here on main since a branch's
# base is one its check has not seen.
# What the check builds from; the project sets LAND_CODE_PATHS to its own.
CODE_PATHS=${LAND_CODE_PATHS:-'^(src|lib|tests|deploy|\.github)/|^(Cargo\.toml|Cargo\.lock|package\.json|package-lock\.json|pyproject\.toml)$'}

# land <number> <head read>: 0 landed, 3 skipped, 4 stop the train.
land() {
  local pr=$1 read=$2 branch head new state title body
  branch=$(field "$pr" headRefName) || return 4
  head=$(field "$pr" headRefOid) || return 4
  case $head in "$read"*) ;; *)
    echo "SKIPPED #$pr: its head $head is not the head read, $read $(now)"
    return 3
    ;;
  esac
  echo "#$pr $branch at $head $(now)"
  git fetch -q origin || return 4
  local behind=0
  if git merge-base --is-ancestor origin/main "$head"; then
    echo "#$pr: the branch contains main"
  elif ! git diff --name-only "$(git merge-base origin/main "$head")" origin/main | grep -Eq "$CODE_PATHS"; then
    behind=1
    echo "#$pr: main gained no code since the branch's base; it lands as it stands"
  else
    echo "#$pr: rebase onto main"
    local base count patch id
    base=$(git merge-base origin/main "$head") || return 4
    count=$(git rev-list --count "$base..$head")
    patch=$(git diff "$base" "$head" | git patch-id --stable | cut -d' ' -f1)
    id=$(field "$pr" id) || return 4
    if ! gh api graphql -F id="$id" -F head="$head" -f query='mutation($id: ID!, $head: GitObjectID!) {
                updatePullRequestBranch(input: {pullRequestId: $id, expectedHeadOid: $head, updateMethod: REBASE}) {
                    pullRequest { headRefOid } } }' -q .data.updatePullRequestBranch.pullRequest.headRefOid; then
      echo "SKIPPED #$pr: the rebase was refused (a conflict, or the head moved) $(now)"
      return 3
    fi
    new=$head
    for _ in $(seq 1 30); do
      sleep 3
      new=$(field "$pr" headRefOid)
      if [ "$new" != "$head" ]; then break; fi
    done
    if [ "$new" = "$head" ]; then
      echo "SKIPPED #$pr: the head did not move $(now)"
      return 3
    fi
    git fetch -q origin || return 4
    if ! git merge-base --is-ancestor origin/main "$new" ||
      [ "$(git rev-list --count "origin/main..$new")" != "$count" ] ||
      [ "$(git diff origin/main "$new" | git patch-id --stable | cut -d' ' -f1)" != "$patch" ]; then
      echo "SKIPPED #$pr: the new head $new is not the head read replayed onto main $(now)"
      return 3
    fi
    head=$new
    echo "#$pr: new head $head"
  fi
  # The check may not exist yet on a head just pushed or updated, and a
  # Dependabot pull request carries a status of its own reported at once,
  # so wait for the check named `check` before watching, up to ten minutes.
  for _ in $(seq 1 120); do
    if [ -n "$(gh pr view "$pr" --json statusCheckRollup -q '.statusCheckRollup[] | select(.name == "check") | .name' 2>/dev/null)" ]; then break; fi
    sleep 5
  done
  if ! gh pr checks "$pr" --watch -i 15; then
    echo "SKIPPED #$pr: the check is red on $head $(now)"
    return 3
  fi
  if [ "$(field "$pr" headRefOid)" != "$head" ]; then
    echo "SKIPPED #$pr: the head moved during the check $(now)"
    return 3
  fi
  # Right after a check completes GitHub recomputes the merge state and
  # answers UNKNOWN for a few seconds.
  for _ in $(seq 1 20); do
    state=$(field "$pr" mergeStateStatus)
    if [ "$state" != UNKNOWN ]; then break; fi
    sleep 3
  done
  if [ "$state" != CLEAN ] && ! { [ "$behind" = 1 ] && [ "$state" = BEHIND ]; }; then
    echo "STOPPED at #$pr: its merge state is $state $(now)"
    return 4
  fi
  title=$(field "$pr" title)
  body=$(git log --no-merges --format='* %s' "origin/main..$head")
  if ! gh pr merge "$pr" --merge --match-head-commit "$head" --subject "$title (PR #$pr)" \
    --body "$body

Co-Authored-By: $co_author"; then
    echo "STOPPED at #$pr: the merge was refused $(now)"
    return 4
  fi
  echo "LANDED #$pr $(now)"
}

skipped=0
for arg in "$@"; do
  land "${arg%%@*}" "${arg#*@}"
  case $? in 3) skipped=1 ;; 4) exit 4 ;; esac
done
echo "DONE $(now)"
if [ "$skipped" = 1 ]; then exit 3; fi
