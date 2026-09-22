# broken-calculator

Seed repository for the durable fix loop. `apply_discount` is wrong on purpose:
it subtracts the percentage as if it were an amount. Three of the four
parametrized cases fail, deterministically, with no network and no clock
dependence.

This directory holds plain files. `scripts/seed_repo.sh` materializes it as a
git repository at a reproducible commit — a nested `.git` here would be an
embedded repository inside the control plane's own repo.
