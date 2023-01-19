import json
import logging
import requests
import os
import re
import gi

gi.require_version('Notify', '0.7')

from gi.repository import Notify
from datetime import datetime
from dateutil.parser import parse
from pytz import timezone
from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent
from ulauncher.api.shared.event import ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction


# define some fixed globals
api_base_url = 'https://api.clockify.me/api/v1'
tracker_url = 'https://clockify.me/tracker'


class ClockifyExtension(Extension):

    def __init__(self):
        super(ClockifyExtension, self).__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(ItemEnterEvent, ItemEventListener())


class KeywordQueryEventListener(EventListener):

    def on_event(self, event, extension):
        items = []
        query = event.get_argument()

        if str(query).split(' ')[0] == 'in':
            items.insert(0, ExtensionResultItem(
                icon='images/icon_go.png',
                name='Resume time tracking',
                description='Create the start of a new time entry, using your last description as title',
                on_enter=ExtensionCustomAction({
                    'call': 'resume'
                })
            ))
            if len(str(query).split(' ')) > 1:
                description = str(query).partition(' ')[2]
                items.insert(0, ExtensionResultItem(
                    icon='images/icon_go.png',
                    name=description,
                    description='Create the start of a new time entry, using this as title',
                    on_enter=ExtensionCustomAction({
                        'call': 'new',
                        'message': description
                    })
                ))
        if str(query).split(' ')[0] == 'out':
            items.insert(0, ExtensionResultItem(
                icon='images/icon_stop.png',
                name='Stop current tracking',
                description='Stops tracking and writes recorded time to Clockify API',
                on_enter=ExtensionCustomAction({
                    'call': 'end'
                })
            ))
            if len(str(query).split(' ')) > 1:
                description = str(query).partition(' ')[2]
                items.insert(0, ExtensionResultItem(
                    icon='images/icon_stop.png',
                    name=description,
                    description='Stop tracking and update title',
                    on_enter=ExtensionCustomAction({
                        'call': 'end_with_update',
                        'message': description
                    })
                ))
        if str(query).split(' ')[0] == 'info':
            items.insert(0, ExtensionResultItem(
                icon='images/icon.png',
                name='Current tracking info',
                description='Get the name and the duration of the currently running tracking',
                on_enter=ExtensionCustomAction({
                    'call': 'info'
                })
            ))

        items.append(ExtensionResultItem(
            icon='images/icon.png',
            name='Open time tracker',
            description='Opens Clockify website in your webbrowser',
            on_enter=OpenUrlAction(url=tracker_url)
        ))

        return RenderResultListAction(items)


class ItemEventListener(EventListener):
    logger = logging.getLogger(__name__)

    def notification_action(self, title, message, mode):
        if self.__notifications_level == 'errors_and_status' and mode != 'error' and mode != 'status':
            return

        Notify.init('ClockifyExtension')
        notif = Notify.Notification.new(title, f"\n{message}", f"{os.path.dirname(__file__)}/images/icon.png")
        if mode == 'error':
            notif.set_urgency(2)
        notif.show()


    def get_now(self):
        raw_time = timezone(self.__user['time_zone']).localize(datetime.now())
        localized_time = str(raw_time.astimezone(timezone('UTC')))[0:-6] + 'Z'
        split_time = localized_time.split(' ')
        split_time.insert(1, 'T')

        return ''.join(split_time)


    def extract_tags(self, message):
        # (?<!\\\) -> negative lookahead, allow for # espcaping. \#abc won't be picked up as a tag
        # #(\w+\-_) -> match tag as one word made out of letters, numbers, -, or _.
        # \s? -> optionally match space after the tag to avoid two spaces when tag in the middle of the message
        reg_exp = "(?<!\\\)#([\w\-_]+)\s?"

        tags = re.findall(reg_exp, message)
        tags = list(dict.fromkeys(tags)) # remove duplicate tags
        message = re.sub(reg_exp, "", message)

        return message, tags

    def get_tag_by_name(self, name):
        response = requests.get(f"{self.__base_workspace_url}/tags?name={name}", headers=self.__headers)
        data = json.loads(response.content.decode('utf-8'))

        if response.status_code == 200:
            return data[0] if len(data) > 0 else None
        else:
            raise RuntimeError(f"Failed to get tag by name '{name}'; Error: {response.status_code}")


    def create_tag(self, name):
        payload = {
            'name': name
        }
        response = requests.post(f"{self.__base_workspace_url}/tags", json=payload, headers=self.__headers)
        if response.status_code == 201:
            return json.loads(response.content.decode('utf-8'))
        else:
            raise RuntimeError(f"Failed to create tag '{name}'; Error: {response.status_code}")

    def get_project_id_by_name(self, name):
        payload = {
            'name': name
        }
        response = requests.get(f"{self.__base_workspace_url}/projects", json=payload, headers=self.__headers)
        data = json.loads(response.content.decode('utf-8'))

        if response.status_code == 200:
            return data[0].id if len(data) > 0 else None
        else:
            raise RuntimeError(f"Failed to get project id by name '{name}'; Error: {response.status_code}")

    def extract_project(self, message):
        reg_exp = "(?<!\\\)@([\w\-_]+)\s?"

        project = re.search(reg_exp, message)
        project = project.group(1)
        message = re.sub(reg_exp, "", message)

        return message, project

    def process_message(self, message):
        (message, project_name) = self.extract_project(message)
        if project_name:
            project_id = self.get_project_id_by_name(project_name)

        (message, tags) = self.extract_tags(message)

        if (len(tags) == 0):
            return message, [], project_id

        tag_ids = []

        for tag_name in tags:
            tag = self.get_tag_by_name(tag_name)
            if tag is None:
                self.logger.debug(f'Creating tag {tag_name}')
                tag = self.create_tag(tag_name)

            self.logger.debug(f"Tag {tag_name}({tag['id']}) will be attached to time entry")
            tag_ids.append(tag['id'])

        return message, tag_ids, project_id


    def get_last_time_entry(self):
        response = requests.get(f"{self.__base_workspace_url}/user/{self.__user['id']}/time-entries?page-size=1", headers=self.__headers)
        time_entries = json.loads(response.content.decode('utf-8'))

        return time_entries[0]


    def get_user(self):
        response = requests.get(api_base_url + '/user', headers=self.__headers)
        user = json.loads(response.content.decode('utf-8'))

        return {
            'id': user['id'],
            'time_zone': user['settings']['timeZone'],
            'default_workspace': user['defaultWorkspace']
        }


    def start_time_entry(self, message):
        try:
            (description, tag_ids, project_id) = self.process_message(message)
        except RuntimeError as e:
            return self.notification_action('Unexpected error', f"{e}", 'error')

        payload = {
            'description': description,
            'tagIds': tag_ids,
            'start': self.get_now(),
            'projectId': project_id if project_id != None else self.__project_id,
        }

        self.logger.debug("Starting time entry: %s", payload)

        response = requests.post(f"{self.__base_workspace_url}/time-entries", json=payload, headers=self.__headers)
        if response.status_code == 201:
            return self.notification_action('Started time entry', description, 'start')
        else:
            return self.notification_action('Could not create new entry', f"Error: HTTP {response.status_code}", 'error')


    def resume_time_entry(self):
        last_time_entry = self.get_last_time_entry()
        payload = {
            'description': last_time_entry['description'],
            'tagIds': last_time_entry['tagIds'],
            'start': self.get_now(),
            'projectId': self.__project_id
        }
        response = requests.post(f"{self.__base_workspace_url}/time-entries", json=payload, headers=self.__headers)
        if response.status_code == 201:
            return self.notification_action('Resuming time entry', last_time_entry['description'], 'start')
        else:
            return self.notification_action('Could not create new entry', f"Error: HTTP {response.status_code}", 'error')


    def end_time_entry(self):
        payload = {
            'end': self.get_now()
        }
        response = requests.patch(f"{self.__base_workspace_url}/user/{self.__user['id']}/time-entries", json=payload, headers=self.__headers)
        if response.status_code == 200:
            data = json.loads(response.content.decode('utf-8'))
            stop_description = data['description']
            stop_time = data['timeInterval']['duration'][2:]
            return self.notification_action('Stopped time tracking', f"{stop_description} (Clocked: {stop_time})", 'stop')
        elif response.status_code == 404:
            return self.notification_action('There is currently no running time entry', 'Get back to work!', 'error')
        else:
            return self.notification_action('Unexpected error', f"HTTP {response.status_code}", 'error')


    def end_time_entry_with_update(self, message):
        try:
            time_entry = self.get_running_time_entry()
        except ValueError:
            return self.notification_action('There is no currently running time entry', 'Get back to work!', 'status')
        except RuntimeError as e:
            return self.notification_action('Unexpected error', f"HTTP {e}", 'error')

        try:
            (description, tag_ids, project_id) = self.process_message(message)
        except RuntimeError as e:
            return self.notification_action('Unexpected error', f"{e}", 'error')

        payload = {
            'description': description,
            'tagIds': tag_ids,
            'projectId': project_id if project_id != None else self.__project_id,
            'start': time_entry['timeInterval']['start'],
            'end': self.get_now(),
        }
        response = requests.put(f"{self.__base_workspace_url}/time-entries/{time_entry['id']}", json=payload, headers=self.__headers)
        if response.status_code == 200:
            data = json.loads(response.content.decode('utf-8'))
            stop_description = data['description']
            stop_time = data['timeInterval']['duration'][2:]
            return self.notification_action('Updated title and stopped time tracking', f"{stop_description} (Clocked: {stop_time})", 'stop')
        else:
            return self.notification_action('Could not stop time tracking', f"Error: HTTP {response.status_code}", 'error')


    def get_running_time_entry(self):
        response = requests.get(f"{self.__base_workspace_url}/user/{self.__user['id']}/time-entries/?in-progress=true",
                                headers=self.__headers)
        if response.status_code == 200:
            time_entries = json.loads(response.content.decode('utf-8'))

            if (len(time_entries) == 0):
                raise ValueError('There is no currently running time entry')
            else:
                return time_entries[0]
        else:
            raise RuntimeError(response.status_code)


    def current_time_entry_info(self):
        try:
            time_entry = self.get_running_time_entry()
        except ValueError:
            return self.notification_action('There is no currently running time entry', 'Get back to work!', 'status')
        except RuntimeError as e:
            return self.notification_action('Unexpected error', f"HTTP {e}", 'error')

        current_description = time_entry['description']
        current_start = parse(time_entry['timeInterval']['start'])
        duration = datetime.now(timezone('UTC')) - current_start
        hours = divmod(duration.total_seconds(), 3600)
        minutes = divmod(hours[1], 60)
        clocked_text = "%iH%iM" % (hours[0], minutes[0])

        return self.notification_action(f"Current time tracking", f"{current_description} (Clocked: {clocked_text})", 'status')


    def on_event(self, event, extension):
        self.__headers = {
            'content-type': 'application/json',
            'X-Api-Key': extension.preferences.get('api_key')
        }
        self.__project_id = extension.preferences.get('project_id')
        self.__notifications_level = extension.preferences.get('notifications_level')
        self.__user = self.get_user()
        self.__base_workspace_url = f"{api_base_url}/workspaces/{self.__user['default_workspace']}"

        call = event.get_data().get('call')
        message = event.get_data().get('message', '')

        if call == 'new':
            return self.start_time_entry(message)

        elif call == 'resume':
            return self.resume_time_entry()

        elif call == 'end':
            return self.end_time_entry()

        elif call == 'end_with_update':
            return self.end_time_entry_with_update(message)

        elif call == 'info':
            return self.current_time_entry_info()


if __name__ == '__main__':
    ClockifyExtension().run()
