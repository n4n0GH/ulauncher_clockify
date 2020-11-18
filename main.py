import json
import requests
import webbrowser
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
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
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
        if str(query).split(' ')[0] == 'status':
            items.insert(0, ExtensionResultItem(
                icon='images/icon.png',
                name='Status of current tracking',
                description='Get the name and the duration of the currently running tracking',
                on_enter=ExtensionCustomAction({
                    'call': 'status'
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


    def find_existing_tags(self):
        response = requests.get(f"{self.__base_workspace_url}/tags", headers=self.__headers)

        return json.loads(response.content.decode('utf-8'))


    def create_tag(self, name):
        payload = {
            'name': name
        }
        response = requests.post(f"{self.__base_workspace_url}/tags", json=payload, headers=self.__headers)
        if response.status_code == 201:
            new_tag = json.loads(response.content.decode('utf-8'))

            return new_tag['id']
        else:
            print(f"Failed to create tag '{name}'; Error: {response.status_code}")


    def process_message(self, message):
        (message, tags) = self.extract_tags(message)
        if (len(tags) == 0):
            return message, []

        existing_tags = self.find_existing_tags()
        matched_tags = list(filter(lambda et : et['name'] in tags, existing_tags))

        matched_tag_names = map(lambda mt: mt['name'], matched_tags)
        tag_ids = list(map(lambda mt: mt['id'], matched_tags))

        for tag in tags:
            if tag in matched_tag_names:
                continue # tag exists, tagId is already known
            else:
                tag_ids.append(self.create_tag(tag))

        return message, tag_ids


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
        (description, tag_ids) = self.process_message(message)
        payload = {
            'description': description,
            'tagIds': tag_ids,
            'start': self.get_now(),
            'projectId': self.__project_id
        }
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


    def status_of_time_entry(self):
        response = requests.get(f"{self.__base_workspace_url}/user/{self.__user['id']}/time-entries/?in-progress=true", headers=self.__headers)
        if response.status_code == 200:
            time_entries = json.loads(response.content.decode('utf-8'))

            if (len(time_entries) == 0):
                return self.notification_action('There is no currently running time entry', 'Get back to work!', 'status')
            else:
                current_description = time_entries[0]['description']
                current_start = parse(time_entries[0]['timeInterval']['start'])
                now = datetime.now(timezone('UTC'))
                duration = now - current_start
                duration_in_s = duration.total_seconds()
                hours = divmod(duration_in_s,3600)
                minutes = divmod(hours[1],60)
                clocked_text = "%iH%iM" % (hours[0], minutes[0])
                return self.notification_action('Current time tracking', f"{current_description} (Clocked: {clocked_text})", 'status')
        else:
            return self.notification_action('Unexpected error', f"HTTP {response.status_code}", 'error')


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

        elif call == 'status':
            return self.status_of_time_entry()


if __name__ == '__main__':
    ClockifyExtension().run()
