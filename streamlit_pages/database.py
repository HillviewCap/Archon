import streamlit as st
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.utils import get_env_var, save_env_var

@st.cache_data
def load_sql_template():
    """Load the SQL template file and cache it"""
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "site_pages.sql"), "r") as f:
        return f.read()


@st.cache_data
def load_llms_txt_sql():
    """Load the llms_txt SQL file and cache it"""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "llms_txt.sql"), "r") as f:
            return f.read()
    except FileNotFoundError:
        return "Error: utils/llms_txt.sql not found."

def get_supabase_sql_editor_url(supabase_url):
    """Get the URL for the Supabase SQL Editor"""
    try:
        # Extract the project reference from the URL
        # Format is typically: https://<project-ref>.supabase.co
        if '//' in supabase_url and 'supabase' in supabase_url:
            parts = supabase_url.split('//')
            if len(parts) > 1:
                domain_parts = parts[1].split('.')
                if len(domain_parts) > 0:
                    project_ref = domain_parts[0]
                    return f"https://supabase.com/dashboard/project/{project_ref}/sql/new"
        
        # Fallback to a generic URL
        return "https://supabase.com/dashboard"
    except Exception:
        return "https://supabase.com/dashboard"


def execute_sql_in_supabase(supabase, sql, operation_name="SQL operation"):
    """Execute SQL directly in Supabase via the exec_sql RPC.

    Args:
        supabase: The Supabase client
        sql: The SQL string to execute
        operation_name: A descriptive name for the operation for logging/error messages

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Note: Assumes an RPC function named 'exec_sql' exists in Supabase
        # that takes a 'query' text argument and executes it.
        response = supabase.rpc("exec_sql", {"query": sql}).execute()

        # Basic check if the response indicates an error (adapt as needed based on actual Supabase client behavior)
        if hasattr(response, 'error') and response.error:
             # Check for specific error indicating the RPC function doesn't exist
            if 'function exec_sql(query => text) does not exist' in str(response.error):
                 return False, f"Error during {operation_name}: The required 'exec_sql' RPC function was not found in your Supabase project. Please follow the setup instructions."
            return False, f"Error during {operation_name}: {response.error.message}"
        # Add more specific success/error checks if the client provides them
        # For now, assume no error attribute means success
        return True, f"{operation_name} completed successfully."
    except Exception as e:
         # Catch potential exceptions during the RPC call itself
         # Check if the error indicates the RPC function doesn't exist
        if 'function exec_sql(query => text) does not exist' in str(e):
             return False, f"Error during {operation_name}: The required 'exec_sql' RPC function was not found in your Supabase project. Please follow the setup instructions."
        return False, f"Error during {operation_name}: An unexpected error occurred - {str(e)}"

def show_rpc_setup_instructions():
    """Show instructions for setting up the required exec_sql RPC function."""
    st.warning("⚠️ One-Time Supabase Setup Required for Automatic Initialization")
    st.markdown("""
    To enable automatic table initialization, a special function (`exec_sql`) needs to be created in your Supabase project's database. This allows the application to securely execute the necessary setup commands.

    **Steps:**
    1. Go to your Supabase project dashboard.
    2. Navigate to the **SQL Editor** section.
    3. Click **New query**.
    4. Paste the following SQL code into the editor:
    """)
    rpc_sql = """-- Create a function to execute arbitrary SQL commands securely
-- This function runs with the permissions of the user who defines it (SECURITY DEFINER)
-- Ensure it's created by a user with sufficient privileges (e.g., postgres)
CREATE OR REPLACE FUNCTION exec_sql(query text)
RETURNS void -- It doesn't return data, just executes the command
LANGUAGE plpgsql
SECURITY DEFINER -- IMPORTANT: Runs with the privileges of the function owner
AS $$
BEGIN
  -- Execute the SQL query passed as an argument
  EXECUTE query;
END;
$$;

-- Optional: Grant execute permission to the authenticated role if needed,
-- but be cautious as this allows any authenticated user to run ANY SQL
-- via this function if they can call it. Consider more specific roles if possible.
-- GRANT EXECUTE ON FUNCTION exec_sql(text) TO authenticated;
            """
    st.code(rpc_sql, language="sql")
    st.markdown("""
    5. Click **Run**.

    Once this function is created, you can use the "Initialize Automatically" buttons in this application. If you encounter errors mentioning `exec_sql` is missing, please ensure you have completed this setup step correctly.
    """)
    st.info("You only need to do this once per Supabase project.")


def show_manual_sql_instructions(sql, vector_dim, recreate=False):
    """Show instructions for manually executing SQL in Supabase"""
    st.info("### Manual SQL Execution Instructions")
    
    # Provide a link to the Supabase SQL Editor
    supabase_url = get_env_var("SUPABASE_URL")
    if supabase_url:
        dashboard_url = get_supabase_sql_editor_url(supabase_url)
        st.markdown(f"**Step 1:** [Open Your Supabase SQL Editor with this URL]({dashboard_url})")
    else:
        st.markdown("**Step 1:** Open your Supabase Dashboard and navigate to the SQL Editor")
    
    st.markdown("**Step 2:** Create a new SQL query")
    
    if recreate:
        st.markdown("**Step 3:** Copy and execute the following SQL:")
        drop_sql = f"DROP FUNCTION IF EXISTS match_site_pages(vector({vector_dim}), int, jsonb);\nDROP TABLE IF EXISTS site_pages CASCADE;"
        st.code(drop_sql, language="sql")
        
        st.markdown("**Step 4:** Then copy and execute this SQL:")
        st.code(sql, language="sql")
    else:
        st.markdown("**Step 3:** Copy and execute the following SQL:")
        st.code(sql, language="sql")
    
    st.success("After executing the SQL, return to this page and refresh to see the updated table status.")

def database_tab(supabase):
    """Display the database configuration interface"""
    st.header("Database Configuration")
    st.write("Set up and manage your Supabase database tables for Archon.")
    
    # Check if Supabase is configured
    if not supabase:
        st.error("Supabase is not configured. Please set your Supabase URL and Service Key in the Environment tab.")
        return
    
    # Expander for RPC setup instructions
    with st.expander("Show/Hide Supabase RPC Setup Instructions", expanded=False):
        show_rpc_setup_instructions()


    # Site Pages Table Setup
    st.subheader("Site Pages Table")
    st.write("This table stores web page content and embeddings for semantic search.")
    
    # Add information about the table
    with st.expander("About the Site Pages Table", expanded=False):
        st.markdown("""
        This table is used to store:
        - Web page content split into chunks
        - Vector embeddings for semantic search
        - Metadata for filtering results
        
        The table includes:
        - URL and chunk number (unique together)
        - Title and summary of the content
        - Full text content
        - Vector embeddings for similarity search
        - Metadata in JSON format
        
        It also creates:
        - A vector similarity search function
        - Appropriate indexes for performance
        - Row-level security policies for Supabase
        """)
    
    # Check if the table already exists
    table_exists = False
    table_has_data = False
    
    try:
        # Try to query the table to see if it exists
        response = supabase.table("site_pages").select("id").limit(1).execute()
        table_exists = True
        
        # Check if the table has data
        count_response = supabase.table("site_pages").select("*", count="exact").execute()
        row_count = count_response.count if hasattr(count_response, 'count') else 0
        table_has_data = row_count > 0
        
        st.success("✅ The site_pages table already exists in your database.")
        if table_has_data:
            st.info(f"The table contains data ({row_count} rows).")
        else:
            st.info("The table exists but contains no data.")
    except Exception as e:
        error_str = str(e)
        if "relation" in error_str and "does not exist" in error_str:
            st.info("The site_pages table does not exist yet. You can create it below.")
        else:
            st.error(f"Error checking table status: {error_str}")
            st.info("Proceeding with the assumption that the table needs to be created.")
        table_exists = False
    
    # Vector dimensions selection
    st.write("### Vector Dimensions")
    st.write("Select the embedding dimensions based on your embedding model:")
    
    vector_dim = st.selectbox(
        "Embedding Dimensions",
        options=[1536, 768, 384, 1024],
        index=0,
        help="Use 1536 for OpenAI embeddings, 768 for nomic-embed-text with Ollama, or select another dimension based on your model."
    )
    
    # Get the SQL with the selected vector dimensions
    sql_template = load_sql_template()
    
    # Replace the vector dimensions in the SQL
    sql = sql_template.replace("vector(1536)", f"vector({vector_dim})")
    
    # Also update the match_site_pages function dimensions
    sql = sql.replace("query_embedding vector(1536)", f"query_embedding vector({vector_dim})")
    
    # Show the SQL
    with st.expander("View SQL", expanded=False):
        st.code(sql, language="sql")
    
    # Create table button
    if not table_exists:
        col_init1, col_init2 = st.columns(2)
        with col_init1:
            if st.button("Get Instructions for Creating Site Pages Table"):
                show_manual_sql_instructions(sql, vector_dim)
        with col_init2:
            if st.button("Initialize Site Pages Table Automatically", key="init_site_pages_auto"):
                with st.spinner("Attempting to create Site Pages table in Supabase..."):
                    success, message = execute_sql_in_supabase(supabase, sql, "Site Pages table creation")
                    if success:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
                        if "'exec_sql' RPC function was not found" in message:
                            st.warning("Please ensure you have followed the RPC setup instructions above.")

        if st.button("Get Instructions for Creating Site Pages Table"):
            show_manual_sql_instructions(sql, vector_dim)
    else:
        # Option to recreate the table or clear data
        col1, col2 = st.columns(2)
        
        with col1:
            st.warning("⚠️ Recreating will delete all existing data.")
            if st.button("Get Instructions for Recreating Site Pages Table"):
                if st.button("Get Instructions for Recreating Site Pages Table"):
                    show_manual_sql_instructions(sql, vector_dim, recreate=True)
                
                if st.button("Recreate Site Pages Table Automatically", key="recreate_site_pages_auto"):
                    st.warning("**Confirm:** This will permanently delete the existing table and all its data before recreating it.")
                    if st.checkbox("I understand and want to proceed with automatic recreation.", key="confirm_recreate_auto"):
                        with st.spinner("Attempting to drop and recreate Site Pages table..."):
                            drop_sql = f"DROP FUNCTION IF EXISTS match_site_pages(vector({vector_dim}), int, jsonb);\nDROP TABLE IF EXISTS site_pages CASCADE;"
                            
                            # Step 1: Drop the table
                            drop_success, drop_message = execute_sql_in_supabase(supabase, drop_sql, "Site Pages table drop")
                            
                            if drop_success:
                                st.info("Existing table and function dropped successfully.")
                                # Step 2: Create the table
                                create_success, create_message = execute_sql_in_supabase(supabase, sql, "Site Pages table recreation")
                                if create_success:
                                    st.success(f"{create_message} Table recreated successfully!")
                                    st.rerun()
                                else:
                                    st.error(f"Error during recreation: {create_message}")
                                    if "'exec_sql' RPC function was not found" in create_message:
                                        st.warning("Please ensure you have followed the RPC setup instructions above.")
                            else:
                                st.error(f"Error dropping existing table: {drop_message}")
                                if "'exec_sql' RPC function was not found" in drop_message:
                                    st.warning("Please ensure you have followed the RPC setup instructions above.")

                show_manual_sql_instructions(sql, vector_dim, recreate=True)
        
        with col2:
            if table_has_data:
                st.warning("⚠️ Clear all data but keep structure.")
                if st.button("Clear Table Data"):
                    try:
                        with st.spinner("Clearing table data..."):
                            # Use the Supabase client to delete all rows
                            response = supabase.table("site_pages").delete().neq("id", 0).execute()
                            st.success("✅ Table data cleared successfully!")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error clearing table data: {str(e)}")
                        # Fall back to manual SQL
                        truncate_sql = "TRUNCATE TABLE site_pages;"
                        st.code(truncate_sql, language="sql")
                        st.info("Execute this SQL in your Supabase SQL Editor to clear the table data.")
                        
                        # Provide a link to the Supabase SQL Editor
                        supabase_url = get_env_var("SUPABASE_URL")
                        if supabase_url:
                            dashboard_url = get_supabase_sql_editor_url(supabase_url)
                            st.markdown(f"[Open Your Supabase SQL Editor with this URL]({dashboard_url})")    

    st.divider()

    # Documentation Retrieval Table Selection
    st.subheader("Documentation Retrieval Table")
    st.write("Select which database table structure to use for documentation retrieval.")

    # Define options and the key for storing the preference
    retrieval_options = {
        "Site Pages (Default - Pydantic AI Docs)": "site_pages",
        "Hierarchical Nodes (llms.txt Framework Docs)": "hierarchical_nodes"
    }
    env_var_key = "DOCS_RETRIEVAL_TABLE"

    # Get current preference from env_vars.json, default to 'site_pages'
    current_preference_value = get_env_var(env_var_key) or "site_pages"

    # Find the display label corresponding to the stored value
    current_preference_label = "Site Pages (Default - Pydantic AI Docs)" # Default label
    for label, value in retrieval_options.items():
        if value == current_preference_value:
            current_preference_label = label
            break

    # Get the index of the current preference for the radio button
    options_list = list(retrieval_options.keys())
    try:
        current_index = options_list.index(current_preference_label)
    except ValueError:
        current_index = 0 # Default to first option if stored value is invalid

    # Callback function to save the selection
    def save_retrieval_preference():
        selected_label = st.session_state.docs_retrieval_table_radio
        selected_value = retrieval_options[selected_label]
        if save_env_var(env_var_key, selected_value):
            st.toast(f"Documentation retrieval table set to: {selected_label}", icon="✅")
        else:
            st.toast(f"Error saving preference for {env_var_key}", icon="❌")

    # Display the radio button
    selected_table_label = st.radio(
        "Select Documentation Table:",
        options=options_list,
        index=current_index,
        key="docs_retrieval_table_radio", # Unique key for session state
        on_change=save_retrieval_preference,
        help="Choose 'Site Pages' for standard web scraping results or 'Hierarchical Nodes' for structured llms.txt processing results."
    )

    st.divider()

    # Hierarchical Nodes Schema Display
    st.subheader("Alternative Schema: Hierarchical Nodes (for llms.txt)")
    with st.expander("View SQL and Instructions", expanded=False):
        st.markdown("""
        This alternative schema is designed for storing hierarchically structured documentation, 
        typically generated by processing `llms.txt` files. It allows for more granular retrieval 
        based on document structure (headers, sections, etc.).
        
        **Instructions:**
        1.  Ensure you have the `pgvector` extension enabled in your Supabase project.
        2.  Run the following SQL commands in your Supabase SQL Editor to create the necessary 
            `hierarchical_nodes` table and related functions/indexes.
        3.  **Important:** Adjust the `VECTOR(1536)` dimension in the SQL below if your embedding model uses a different dimension (e.g., 768 for `nomic-embed-text`).
        """)
        
        # Load and display the llms_txt SQL
        llms_txt_sql = load_llms_txt_sql()
        if "Error:" in llms_txt_sql:
            st.error(llms_txt_sql)
        else:
            # Replace vector dimension placeholder if needed (similar to site_pages)
            # Assuming the same vector_dim variable applies, otherwise fetch separately
            llms_txt_sql_adjusted = llms_txt_sql.replace("VECTOR(1536)", f"VECTOR({vector_dim})")
            st.code(llms_txt_sql_adjusted, language="sql")

        # Provide a link to the Supabase SQL Editor
        supabase_url = get_env_var("SUPABASE_URL")
        if supabase_url:
            dashboard_url = get_supabase_sql_editor_url(supabase_url)
            st.markdown(f"[Open Your Supabase SQL Editor]({dashboard_url})")
        else:
            st.warning("Configure Supabase URL in Environment tab to get a direct link to the SQL Editor.")

            # Button for automatic initialization
            if st.button("Initialize Hierarchical Nodes Table Automatically", key="init_hierarchical_auto"):
                if "Error:" in llms_txt_sql:
                    st.error("Cannot initialize automatically: SQL file not found or contains errors.")
                else:
                    llms_txt_sql_adjusted = llms_txt_sql.replace("VECTOR(1536)", f"VECTOR({vector_dim})")
                    with st.spinner("Attempting to create Hierarchical Nodes table in Supabase..."):
                        success, message = execute_sql_in_supabase(supabase, llms_txt_sql_adjusted, "Hierarchical Nodes table creation")
                        if success:
                            st.success(message)
                            # Consider adding a check here to see if the table now exists and update UI
                            # For now, just show success message.
                        else:
                            st.error(message)
                            if "'exec_sql' RPC function was not found" in message:
                                st.warning("Please ensure you have followed the RPC setup instructions above.")


