from src.utils.supabase_client import DatabaseManager

supabase = DatabaseManager.get_client()

# Check agents
agents = supabase.table('agents').select('*').execute()
print("Agents:")
for a in agents.data:
    print(a)

print("\nSoftware Versions:")
versions = supabase.table('software_versions').select('*').execute()
for v in versions.data:
    print(v)
