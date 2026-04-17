# Lesson: Check the data before designing the fix

**Date**: 2026-04-10
**Source**: rrr: fb-crawler / x-crawler
**Tags**: #analysis #planning #efficiency

## Pattern

When the user reports a problem in vague terms ("tons of duplicates", "it's slow", "lots of errors"), spend 2-5 minutes querying the actual data before drafting a plan. The user's intuition is usually directionally right but rarely precise about *what* needs fixing.

## Evidence

User said "tons of duplicates" in fb-crawler. DB query showed:
- 0 actual duplicate comments (post-id dedup already worked)
- 50 duplicate post entries across export files (real, but minor)
- 0% wasted scrapes so far (only 18 scrapes, but waste rate would spike as DB matures)

The real problem wasn't duplicates — it was that the architecture wastes more effort over time. Reframing this to the user before coding led to a better plan covering all 4 friction points (early exit, smart deep, dedup-on-update, export cleanup) instead of just "remove duplicates."

## Application

Before any optimization or "cleanup" task: run the queries first, then plan. Show numbers in the plan to ground the conversation.
