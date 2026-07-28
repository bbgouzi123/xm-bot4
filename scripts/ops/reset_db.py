from src.utils.account_db import AccountDatabaseManager
if __name__ == '__main__':
    from src.crm.account_data import get_active_account
    account_id = get_active_account()
    db = AccountDatabaseManager(account_id)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM bot_config WHERE config_key = 'industry_config_data'")
        conn.commit()
    print("Deleted industry_config_data successfully.")
