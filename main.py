import json
import requests
import webbrowser
import os
import gi

gi.require_version('Notify', '0.7')

from gi.repository import Notify
from datetime import datetime
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
apiBaseUrl = 'https://api.clockify.me/api/v1'
trackerUrl = 'https://clockify.me/tracker'
cwd = os.path.dirname(__file__)
recoveryFile = os.path.join(cwd, 'recovery.json')
clockFile = os.path.join(cwd, 'clock.json')


class ClockifyExtension(Extension):

    def __init__(self):
        super(ClockifyExtension, self).__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(ItemEnterEvent, ItemEventListener())


class KeywordQueryEventListener(EventListener):

    def on_event(self, event, extension):
        items = []
        query = event.get_argument()
        projectId = extension.preferences.get('project_id')
        apiKey = extension.preferences.get('api_key')

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

        items.append(ExtensionResultItem(
            icon='images/icon.png',
            name='Open time tracker',
            description='Opens Clockify website in your webbrowser',
            on_enter=OpenUrlAction(url=trackerUrl)
        ))
        return RenderResultListAction(items)


class ItemEventListener(EventListener):

    def NotificationAction(self, message):
        Notify.init('ClockifyExtension')
        Notify.Notification.new('Clockify', message).show()

    def getTime(self, time):
        rawTime = timezone(time).localize(datetime.now())
        localizedTime = str(rawTime.astimezone(timezone('UTC')))[0:-6] + 'Z'
        splitTime = localizedTime.split(' ')
        splitTime.insert(1, 'T')
        return ''.join(splitTime)

    def on_event(self, event, extension):
        # oh lawd help me I have no idea what I'm doing
        reqHeader = {
            'content-type': 'application/json',
            'X-Api-Key': extension.preferences.get('api_key')
        }
        userResponse = requests.get(apiBaseUrl + '/user', headers=reqHeader)
        user = json.loads(userResponse.content.decode('utf-8'))
        userId = user['id']
        userTz = user['settings']['timeZone']
        workspaceId = user['defaultWorkspace']
        workspaceResponse = requests.get(apiBaseUrl + '/workspaces/' + workspaceId + '/user/' + userId + '/time-entries', headers=reqHeader)
        timeEntries = json.loads(workspaceResponse.content.decode('utf-8'))

        data = event.get_data()

        if data['call'] == 'new':
            newPayload = {
                'description': data['message'],
                'start': self.getTime(userTz),
                'projectId': extension.preferences.get('project_id')
            }
            startResponse = requests.post(apiBaseUrl + '/workspaces/' + workspaceId + '/time-entries', headers=reqHeader, json=newPayload)
            if startResponse.status_code == 201:
                return self.NotificationAction('Started time entry for ' + data['message'])
            else:
                print(newPayload)
                return self.NotificationAction('Could not create new entry: HTTP ' + str(startResponse.status_code))
        elif data['call'] == 'resume':
            # TODO fetch last used time entry's description
            resumePayload = {
                'description': timeEntries[0]['description'],
                'start': self.getTime(userTz),
                'projectId': extension.preferences.get('project_id')
            }
            resumeResponse = requests.post(apiBaseUrl + '/workspaces/' + workspaceId + '/time-entries', headers=reqHeader, json=resumePayload)
            if resumeResponse.status_code == 201:
                return self.NotificationAction('Resuming time entry "' + timeEntries[0]['description'] + '"')
            else:
                print(resumePayload)
                return self.NotificationAction('Could not create new entry: HTTP ' + str(resumeResponse.status_code))
        elif data['call'] == 'end':
            stopPayload = {
                'end': self.getTime(userTz)
            }
            stopResponse = requests.patch(apiBaseUrl + '/workspaces/' + workspaceId + '/user/' + userId + '/time-entries', headers=reqHeader, json=stopPayload)
            if stopResponse.status_code == 200:
                return self.NotificationAction('Stopped time tracking')
            else:
                print(stopPayload)
                return self.NotificationAction('Who said you could stop? HTTP ' + str(stopResponse.status_code) + ': Get back to work!')


if __name__ == '__main__':
    ClockifyExtension().run()
