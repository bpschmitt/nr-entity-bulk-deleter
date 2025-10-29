import requests
import json
import time
import argparse
import sys

# --- Fixed Configuration ---
NERDGRAPH_ENDPOINT = "https://api.newrelic.com/graphql"

# The Entity Search Query (Step 1 from the markdown file)
# It takes the dynamic query string as a variable.
ENTITY_SEARCH_QUERY = """
query FindMatchingEntities($entityQuery: String!) {
  actor {
    entitySearch(query: $entityQuery) {
      results {
        entities {
          guid
          name
          entityType
          domain
        }
      }
    }
  }
}
"""

# --- GraphQL Mutation Definitions (Step 2) ---

# Mapping entityType to the correct deletion mutation
DELETION_MUTATIONS = {
    "DASHBOARD_ENTITY": """
        mutation DeleteDashboard($guid: EntityGuid!) {
            dashboardDelete(guid: $guid) {
                status
            }
        }
    """,
    # FIX: Removed the nested { guid } selection. It now returns a list of scalar GUID strings.
    "APM_APPLICATION_ENTITY": """
        mutation DeleteGenericEntity($guids: [EntityGuid!]!) {
            entityDelete(guids: $guids) {
                deletedEntities
            }
        }
    """,
    # FIX: Removed the nested { guid } selection. It now returns a list of scalar GUID strings.
    "INFRASTRUCTURE_HOST_ENTITY": """
        mutation DeleteGenericEntity($guids: [EntityGuid!]!) {
            entityDelete(forceDelete: true, guids: $guids) {
                deletedEntities
            }
        }
    """,
    "THIRD_PARTY_SERVICE_ENTITY": """
        mutation DeleteGenericEntity($guids: [EntityGuid!]!) {
            entityDelete(forceDelete: true, guids: $guids) {
                deletedEntities
            }
        }
    """,
    # Add more mutations based on your search needs (e.g., WORKLOAD_ENTITY, ALERT_CONDITION_ENTITY)
}

def execute_graphql(query, variables=None, api_key=None, max_retries=3):
    """Executes a GraphQL query or mutation with basic exponential backoff."""
    headers = {
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                NERDGRAPH_ENDPOINT,
                headers=headers,
                json={"query": query, "variables": variables or {}}
            )
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                # Filter out specific GraphQL errors that prevent retries
                print(f"[ERROR] GraphQL Errors on attempt {attempt + 1}: {data['errors']}")

                # Simple check for transient errors vs permission/syntax errors
                is_transient_error = any("timeout" in str(e).lower() for e in data['errors'])

                if is_transient_error and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                # If not transient or max retries reached, return the error
                return data

            return data

        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Request failed on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            return None
    return None


def bulk_delete_entities(api_key, account_id, entity_query_string):
    """Main function to perform the two-step bulk deletion."""
    print(f"--- Step 1: Searching for entities matching: {entity_query_string} (Account ID: {account_id}) ---")

    # 1. Execute the Entity Search Query
    # Define the variables dynamically based on the input query string
    search_variables = {"entityQuery": entity_query_string}

    # Pass the API key to the execution function
    search_data = execute_graphql(ENTITY_SEARCH_QUERY, search_variables, api_key)

    if not search_data or not search_data.get('data'):
        print("Failed to retrieve search results or no 'data' in response.")
        return

    # Check for specific search error (e.g., unauthorized)
    if 'errors' in search_data:
        print(f"FATAL ERROR during search. Check your API Key or Query syntax. Errors: {search_data['errors']}")
        return

    entities = search_data['data']['actor']['entitySearch']['results']['entities']

    if not entities:
        print("No entities found matching the criteria. Nothing to delete.")
        return

    print(f"Found {len(entities)} entities to delete.")

    # 2. Loop through entities and execute deletion mutation
    print("\n--- Step 2: Executing Deletion Mutations ---")

    deleted_count = 0

    for entity in entities:
        guid = entity['guid']
        name = entity['name']
        entity_type = entity['entityType']

        # Determine the correct mutation based on entityType
        mutation_query = DELETION_MUTATIONS.get(entity_type)

        if not mutation_query:
            print(f"[SKIP] No specific mutation defined for type {entity_type} (Entity: {name}, GUID: {guid}). Skipping.")
            continue

        print(f"   -> Deleting {entity_type} '{name}' ({guid})...", end=" ")

        # Prepare variables: entityDelete requires the GUID to be passed in a list
        if entity_type == "DASHBOARD_ENTITY":
             # dashboardDelete expects a single scalar 'guid'
            deletion_variables = {"guid": guid}
        else:
            # entityDelete expects a list of guids 'guids'
            deletion_variables = {"guids": [guid]}

        # Execute the Deletion Mutation, passing the API key
        delete_result = execute_graphql(mutation_query, deletion_variables, api_key)

        # Assume success unless we hit a top-level GraphQL error or the GUID isn't returned in the result.
        is_successful = False
        error_msg = "Deletion failed (check permissions/manual deletion status)." # Default error

        if delete_result and 'errors' not in delete_result:

            # Case 1: Dashboard deletion check
            if entity_type == "DASHBOARD_ENTITY" and delete_result['data']['dashboardDelete']['status'] == "SUCCESS":
                 is_successful = True

            # Case 2: Generic entity deletion check (APM, Infra, etc.)
            elif 'entityDelete' in delete_result['data']:
                # 'deletedEntities' is now a list of GUID strings.
                deleted_guids = delete_result['data']['entityDelete'].get('deletedEntities', [])

                # Check if the GUID string is directly present in the list of strings.
                if guid in deleted_guids:
                    is_successful = True
                else:
                    # REFINEMENT: If deletion was attempted but the GUID wasn't confirmed deleted,
                    # provide a more helpful message, especially for Infra Hosts.
                    error_msg = f"Deletion request accepted, but GUID {guid} not returned as deleted. This often happens if the Infrastructure Host is still reporting data and was instantly recreated."


        # Final output based on success flag
        if is_successful:
            print("SUCCESS.")
            deleted_count += 1
        else:
            # If we failed due to a top-level GraphQL error (e.g., syntax, permission)
            if delete_result and delete_result.get('errors'):
                error_msg = delete_result['errors'][0]['message']

            print(f"FAILED. Error: {error_msg}")

    print(f"\n--- Deletion Complete: {deleted_count} of {len(entities)} entities successfully deleted. ---")

def parse_args():
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(
        description="New Relic NerdGraph Bulk Entity Deleter by Name Pattern."
    )
    parser.add_argument(
        '-k', '--api-key',
        required=True,
        help='Your New Relic User API Key (NRAK-...) with APM and Configuration access.'
    )
    parser.add_argument(
        '-a', '--account-id',
        required=True,
        type=int,
        help='Your New Relic Account ID (Integer).'
    )
    # New argument for the query string
    parser.add_argument(
        '-q', '--query',
        required=True,
        help="The full NerdGraph entitySearch query string (e.g., \"name LIKE 'staging-app%' AND domain='APM'\"). NOTE: Use quotes around the query if it contains spaces or special characters."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        # Pass the new query argument to the main function
        bulk_delete_entities(args.api_key, args.account_id, args.query)
    except Exception as e:
        print(f"\nAn unrecoverable error occurred: {e}", file=sys.stderr)
        sys.exit(1)

