"""
Handlers package for the Collections Bot.
This package divides bot logic into commands, callbacks, browse handlers, and messages.
"""
from .commands import (
    start, new_collection, list_collections, manage_collections, browse,
    remove, id_file, access_shared
)
from .callbacks import (
    handle_select_collection_callback,
    handle_collection_send_all_callback,
    handle_stop_collect_callback,
    handle_main_menu_button, handle_back_to_main_callback,
    handle_manage_collection_callback, handle_share_collection_callback,
    handle_share_stats_callback, handle_regenerate_share_callback,
    handle_revoke_share_callback, handle_export_collection_callback,
    handle_delete_collection_callback, handle_back_to_manage_callback,
    handle_exit_shared_collection_callback, handle_cancel_share_access_callback,
    handle_exit_delete_mode_callback, handle_select_item_delete_col_callback,
    handle_set_share_expiration_callback, handle_save_share_expiration_callback,
    handle_custom_share_expiration_callback, handle_save_custom_share_expiration_callback,
    handle_collection_info_callback
)
from .duplicate_handlers import (
    handle_scan_duplicates_callback,
    handle_confirm_delete_dupes_callback,
    handle_dupes_page_callback
)
from .browse_handlers import (
    handle_browse_page_callback, handle_scroll_view_callback,
    handle_page_info_callback, handle_back_to_info_callback,
    handle_browse_group_or_select_all_callback, handle_page_file_send_choice_callback,
    handle_batch_status_callback, handle_random_video_scroll_callback,
    handle_search_collection_callback
)
from .messages import (
    handle_message, handle_new_collection_name_input,
    handle_import_col_callback
)

# Export list
__all__ = [
    # Commands
    "start", "new_collection", "list_collections", "manage_collections",
    "browse", "remove", "id_file", "access_shared",

    # Callbacks
    "handle_select_collection_callback", "handle_browse_page_callback",
    "handle_scroll_view_callback", "handle_page_info_callback",
    "handle_search_collection_callback",
    "handle_browse_group_or_select_all_callback", "handle_page_file_send_choice_callback",
    "handle_batch_status_callback", "handle_collection_send_all_callback",
    "handle_stop_collect_callback", "handle_random_video_scroll_callback",
    "handle_main_menu_button", "handle_back_to_main_callback",
    "handle_manage_collection_callback", "handle_share_collection_callback",
    "handle_share_stats_callback", "handle_regenerate_share_callback",
    "handle_revoke_share_callback", "handle_export_collection_callback",
    "handle_delete_collection_callback", "handle_back_to_manage_callback",
    "handle_exit_shared_collection_callback", "handle_cancel_share_access_callback",
    "handle_exit_delete_mode_callback", "handle_import_col_callback",
    "handle_select_item_delete_col_callback", "handle_back_to_info_callback",
    "handle_set_share_expiration_callback", "handle_save_share_expiration_callback",
    "handle_custom_share_expiration_callback", "handle_save_custom_share_expiration_callback",
    "handle_collection_info_callback",
    "handle_scan_duplicates_callback", "handle_confirm_delete_dupes_callback",
    "handle_dupes_page_callback",

    # Messages
    "handle_message", "handle_new_collection_name_input"
]
