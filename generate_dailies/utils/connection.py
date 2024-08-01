import requests


class Connection:
    def __init__(self, username=None, password=None, token=None):
        self.API_URL = "http://192.168.15.51:5001"
        self.username = username
        self.password = password
        self.token = token
        if not token:
            self.token = self.login(self.username, self.password)
        self.attribute_list = None
        self.datalist = dict()

        if self.attribute_list == None:
            self.attribute_list = self.get_attribute_codec(getcodec=False)

    def login(self, username, password):
        url = f"{self.API_URL}/Auth/login"
        payload = {"username": username, "password": password}

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            token = response.json()["access_token"]
            # print("Token:", f"Bearer {token}")
            return token
        except requests.exceptions.RequestException as e:
            print("Error occurred:", e)
            return None
        finally:
            response.close()

    def get_slate_configuration(self, proj_code=None, daily_type=None):
        url = f"{self.API_URL}/Others/v1/slate_configuration"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "proj_code": proj_code,
            "dailies_type": daily_type,
            "islatest": "true",
        }
        try:
            response = requests.get(url, headers=headers, params=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("dailies_config")
        except requests.exceptions.RequestException as e:
            print("Error occurred:", e)
            return None
        finally:
            response.close()

    def get_attribute_codec(self, getcodec=False):
        url = f"{self.API_URL}/Others/v1/slate_configuration"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"isconfig": "true"}
        try:
            response = requests.get(url, headers=headers, params=payload)
            response.raise_for_status()
            data = response.json()
            if getcodec:
                return data.get("output_codecs")
            else:
                return data.get("attribute_list")
        except requests.exceptions.RequestException as e:
            print("Error occurred:", e)
            return None
        finally:
            response.close()

    def get_datalist(self, scope_name=None, proj_code=None, task_id=None):
        self.get_scope_by_scopename(scope_name, proj_code)
        self.get_task_data_by_task_id(task_id=task_id, proj_code=proj_code)
        self.get_notes(proj_code=proj_code, task_id=task_id)
        return self.datalist

    def get_scope_by_scopename(self, scope_name, proj_code):
        url = f"{self.API_URL}/Scopes/v1/scope_by_scope_name"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"scope_name": scope_name, "proj_code": proj_code}

        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            for i in self.attribute_list["scope_table"]:
                self.datalist.update({i: data[0][i]})
        except requests.exceptions.RequestException as e:
            print("Error occurred:", e)
            return None
        finally:
            response.close()

    def get_task_data_by_task_id(self, task_id=None, proj_code=None):
        url = f"{self.API_URL}/Task/v1/get_task_data_by_task_id"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"task_id": task_id, "proj_code": proj_code}

        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            for i in self.attribute_list["task_table"]:
                self.datalist.update({i: data[0][i]})

        except requests.exceptions.RequestException as e:
            print("Error occurred:", e)
            return None
        finally:
            response.close()

    def get_notes(self, proj_code=None, task_id=None, created=None):
        url = f"{self.API_URL}/Others/v1/notes"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"proj_code": proj_code, "task_id": task_id}

        try:
            response = requests.get(url, headers=headers, params=payload)
            response.raise_for_status()
            data = response.json()
            for i in self.attribute_list["notes_table"]:
                self.datalist.update({i: data[0][i]})
        except requests.exceptions.RequestException as e:
            print("Error occurred:", e)
            return None
