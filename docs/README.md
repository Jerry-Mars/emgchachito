# Project Documentation

This directory separates documentation by purpose so that current contracts do
not get mixed with one-time investigation notes.

## Documentation sets

- [Development Wiki](development-wiki/README.md) — current architecture,
  runtime lifecycle, data contracts, extension procedures, testing, and a
  staged refactoring guide. Start here when changing code.
- [Manuals](manuals/) — operator-facing instructions for running experiments.
- [Engineering Notes](engineering-notes/README.md) — dated reviews of captures,
  design decisions, limitations, and historical investigations.

## Which document is authoritative?

For current implementation details, use the Development Wiki and verify the
named symbol in code. Manuals are authoritative for intended operator behavior.
Engineering notes preserve the context of a decision at a specific date and
may describe an older device address or experiment configuration.

When code changes a public data schema, lifecycle rule, default device setup,
or operator workflow, update the corresponding Development Wiki page and
manual in the same commit.
