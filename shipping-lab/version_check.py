"""Compare version strings and expose the comparison as a CLI."""

import argparse


def same_version(observed, expected):
    return bool(observed and expected and observed.startswith(expected))


def main():
    parser = argparse.ArgumentParser(description="Compare two version strings.")
    parser.add_argument("observed")
    parser.add_argument("expected")
    args = parser.parse_args()
    accepted = same_version(args.observed, args.expected)
    print("accepted" if accepted else "rejected")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
