## nr-entity-bulk-deleter

A simple script to bulk delete entities in New Relic.

```bash
usage: bulk-deleter.py [-h] -k API_KEY -a ACCOUNT_ID -q QUERY
bulk-deleter.py: error: the following arguments are required: -k/--api-key, -a/--account-id, -q/--query
```

## Example

```bash
$ python3 bulk-deleter.py -k NRAK-xxxxxx -a 1234567 --query "name in ('ip-192-168-20-136.ec2.internal') and domain in ('INFRA')"
--- Step 1: Searching for entities matching: name in ('ip-192-168-20-136.ec2.internal') and domain in ('INFRA') (Account ID: 1234567) ---
Found 1 entities to delete.

--- Step 2: Executing Deletion Mutations ---
   -> Deleting INFRASTRUCTURE_HOST_ENTITY 'ip-192-168-20-136.ec2.internal' (NDQ1N<REDACTED>)... SUCCESS.

--- Deletion Complete: 1 of 1 entities successfully deleted. ---
```