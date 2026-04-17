# Lesson: Ask for SSH credentials, don't guess

**Date**: 2026-04-10
**Source**: rrr: fb-crawler
**Tags**: #infrastructure #ssh #ec2 #efficiency

## Pattern

When the user says "also do X on EC2/remote", immediately ask for:
1. SSH key path
2. Username
3. Host IP
4. Remote file path

Don't cycle through known_hosts entries and PEM files — the combinatorial space is too large and wastes time.

## Evidence

Spent 10 minutes trying 4 keys × 3 users × 3 hosts = 36 combinations. User provided the answer in one message. The actual remote edit took 30 seconds.

## Application

Any future task involving remote servers in this user's infrastructure.
