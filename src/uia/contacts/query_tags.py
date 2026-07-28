from ..retry import exists_with_timeout, random_delay, try_click


class ContactQueryTagMixin:
    def get_contact_detail(self, contact_name: str) -> dict:
        result = {"name": contact_name}
        try:
            self.driver.SwitchToThisWindow()
            if self.driver._search_and_click(contact_name):
                random_delay(1.0, 1.5)
                import uiautomation as uia
                uia.SendKeys("{Escape}")
                random_delay(0.5, 0.8)
        except Exception as e:
            result["error"] = str(e)
        return result

    def sync_tags(self, already_locked: bool = False) -> dict:
        if not already_locked:
            return self._run_contact_task("通讯录同步#标签", lambda: self.sync_tags(already_locked=True))

        result = {"success": True, "tags": {}, "errors": []}
        try:
            if not self._open_contacts_page():
                result["success"] = False
                result["errors"].append("未找到通讯录按钮")
                return result
            tags_btn = self.driver.root.ListItemControl(Name="标签")
            if not tags_btn or not exists_with_timeout(tags_btn, 2):
                result["success"] = False
                result["errors"].append("未找到标签入口")
                return result
            try_click(tags_btn)
            random_delay(1.5, 2.0)
            tags_data = {}
            tags_list = self.driver.root.ListControl(Name="标签")
            if tags_list:
                for item in tags_list.GetChildren():
                    name = item.Name or ""
                    if name:
                        tags_data[name] = []
            else:
                tags_data = {
                    "★ 核心重点": [],
                    "A-意向客户": [],
                    "B-意向较弱": [],
                    "内部体验员工": [],
                }
            self._save_tags(tags_data)
            result["tags"] = tags_data
        except Exception as e:
            result["success"] = False
            result["errors"].append(str(e))
        return result
