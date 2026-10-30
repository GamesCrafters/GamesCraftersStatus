#!/usr/bin/env bash
# Git plumbing for the `data` branch, shared by the probe and cleanup workflows.
#
#   data-branch.sh checkout <repo-dir> <data-dir>
#       Check out the data branch of <repo-dir>'s origin into <data-dir> as a
#       worktree, or start it as an empty branch if it does not exist yet.
#   data-branch.sh push <data-dir> <message>
#       Commit everything in <data-dir> and push it. If the push is rejected
#       because the branch moved, rebase on it and try again (3 attempts).
#
# A worktree shares <repo-dir>'s git config, so it pushes with the same
# credentials actions/checkout set up for <repo-dir>.
set -euo pipefail

BRANCH="${DATA_BRANCH:-data}"
BOT_NAME="github-actions[bot]"
BOT_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"

absolute() {
  mkdir -p "$(dirname "$1")"
  echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
}

fetch_branch() {
  git fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
}

checkout() {
  local repo="$1" dir
  dir="$(absolute "$2")"
  cd "$repo"
  local rc=0
  git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null || rc=$?
  if [ "$rc" -eq 0 ]; then
    fetch_branch
    git worktree add --quiet -B "$BRANCH" "$dir" "origin/$BRANCH"
    echo "checked out $BRANCH into $dir"
  elif [ "$rc" -eq 2 ]; then
    # Exit code 2 means the branch does not exist. Anything else (network,
    # auth) is an error: never start a new branch just because origin was
    # unreachable.
    git worktree add --quiet --detach "$dir"
    cd "$dir"
    git checkout --quiet --orphan "$BRANCH"
    git rm -r --quiet --cached . >/dev/null 2>&1 || true
    find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    echo "started a new $BRANCH branch in $dir"
  else
    echo "could not reach origin to look for $BRANCH (git exit code $rc)" >&2
    return "$rc"
  fi
}

push() {
  local dir="$1" message="$2"
  cd "$dir"
  git add -A
  if git diff --cached --quiet; then
    echo "nothing to commit"
    return 0
  fi
  git -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" commit --quiet -m "$message"
  for attempt in 1 2 3; do
    if git push --quiet origin "HEAD:refs/heads/$BRANCH"; then
      echo "pushed to $BRANCH"
      return 0
    fi
    echo "push rejected (attempt $attempt of 3); rebasing on the latest $BRANCH"
    sleep $((attempt * 5))
    fetch_branch
    if ! git -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" rebase --quiet "origin/$BRANCH"; then
      git rebase --abort || true
      echo "rebase hit a conflict; not pushing" >&2
      return 1
    fi
  done
  echo "push failed 3 times" >&2
  return 1
}

case "${1:-}" in
  checkout) checkout "$2" "$3" ;;
  push) push "$2" "$3" ;;
  *) echo "usage: $0 checkout <repo-dir> <data-dir> | push <data-dir> <message>" >&2; exit 2 ;;
esac
