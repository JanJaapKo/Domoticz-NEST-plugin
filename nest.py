#!/usr/bin/env python3
#
# Nest Class to access the Nest thermostat and Protect through the Google account.
#
# Based on https://github.com/gboudreau/nest-api
#
# Author: Filip Demaertelaere
# Extended by: Mark Ruys
#
# The values of issue_token and cookies are specific to your Google account.
# To get them, follow these steps (only needs to be done once, as long as you stay logged into your Google account).
#
#     Open a Chrome browser tab in Incognito Mode (or clear your cache).
#     Open Developer Tools (View/Developer/Developer Tools).
#     Click on Network tab. Make sure Preserve Log is checked.
#     In the Filter box, enter issueToken
#     Go to https://home.nest.com, and click Sign in with Google. Log into your account.
#     One network call (beginning with iframerpc) will appear in the Dev Tools window. Click on it.
#     In the Headers tab, under General, copy the entire Request URL (beginning with https://accounts.google.com, ending with nest.com). This is your $issue_token.
#     In the Filter box, enter oauth2/iframe
#     Several network calls will appear in the Dev Tools window. Click on the last iframe call.
#     In the Headers tab, under Request Headers, copy the entire cookie value (include the whole string which is several lines long and has many field/value pairs - do not include the Cookie: prefix). This is your $cookies; make sure all of it is on a single line.

import time
from datetime import datetime
import json
import requests
import traceback
import pytz
import tzlocal

try:
    import Domoticz
    def log(msg=""):
        if len(msg) <= 5000:
            Domoticz.Debug(">> {}".format(msg))
        else:
            Domoticz.Debug(">> (in several blocks)")
            string = [msg[i:i+5000] for i in range(0, len(msg), 5000)]
            for k in string:
                Domoticz.Debug(">> {}".format(k))
except:
    def log(msg=""):
        print(msg)

class Nest():

    USER_AGENT = 'Domoticz Nest/1.0'
    REQUEST_TIMEOUT = 5.0

    def __init__(self, issue_token, cookie, refresh_time):
        self._issue_token = issue_token
        self._cookie = cookie
        self._refresh_time = refresh_time
        self._access_token = None
        self._access_token_type = None
        self._nest_user_id = None
        self._nest_access_token = None
        self._transport_url = None
        self._user = None
        self._cache_expiration_text = None
        self._cache_expiration = None
        self._id_token = None
        self._status = None
        self._running = True
        self._nest_access_error = None
        #self._ReadCache()
        self.device_list = []
        self.protect_list = []
        self.country_code = None
        self.postal_code = None

    def terminate(self):
        self._running = False

#     def _ReadCache(self):
#         if os.path.exists('nest.json'):
#             try:
#                 with open('nest.json') as json_file:
#                     data = json.load(json_file)
#                     self._nest_user_id = data['user_id']
#                     self._nest_access_token = data['access_token']
#                     self._transport_url = data['transport_url']
#                     self._user = data['user']
#                     self._cache_expiration_text = data['cache_expiration'] #2019-11-23T11:16:51.640Z
#                     self._cache_expiration = datetime.strptime(self._cache_expiration, '%Y-%m-%dT%H:%M:%S.%fZ')
#             except:
#                 pass

#     def _WriteCache(self):
#         try:
#             with open('nest.json', 'w') as json_file:
#                 json.dump({ 'user_id': self_nest_user_id,
#                             'access_token': self._nest_access_token,
#                             'user': self._user,
#                             'transport_url': self._transport_url,
#                             'cache_expiration': self._cache_expiration_text
#                           }, json_file)
#         except:
#             pass

    def _GetBearerTokenUsingGoogleCookiesIssue_token(self):
        if not self._running:
            return False

        url = self._issue_token
        headers = {
            'Sec-Fetch-Mode': 'cors',
            'User-Agent': self.USER_AGENT,
            'X-Requested-With': 'XmlHttpRequest',
            'Referer': 'https://accounts.google.com/o/oauth2/iframe',
            'Cookie': self._cookie,
        }
        try:
            request = requests.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT)
            request.raise_for_status()
            result = request.json()
            if 'error' in result and 'detail' in result:
                if result['error'] == 'USER_LOGGED_OUT':
                    self._nest_access_error = 'API returned error: {}'.format(result['detail'])
                else:
                    self._nest_access_error = 'Invalid IssueToken/Cookie: %s (%s)' % (result['error'], result['detail'])
            elif 'access_token' in result and 'token_type' in result and 'id_token' in result:
                self._access_token = result['access_token']
                self._access_token_type = result['token_type']
                self._id_token = result['id_token']
                self._nest_access_error = None
                log("Got bearer token")
                return True

        except requests.exceptions.Timeout as e:
            self._nest_access_error = 'API request timed out'
        except requests.exceptions.ConnectionError as e:
            self._nest_access_error = 'Connection error API request'
        except requests.exceptions.HTTPError as e:
            self._nest_access_error = 'API request failed (status {})'.format(e.response.status_code)
        except json.JSONDecodeError as e:
            self._nest_access_error = 'Invalid API response'
        return False

    def _UseBearerTokenToGetAccessTokenAndUserId(self):
        if not self._running:
            return False

        url = 'https://nestauthproxyservice-pa.googleapis.com/v1/issue_jwt'
        data = {
            'embed_google_oauth_access_token': True,
             'expire_after': '3600s',
             'google_oauth_access_token': self._access_token,
             'policy_id': 'authproxy-oauth-policy',
        }
        headers = {
            'Authorization': self._access_token_type + ' ' + self._access_token,
            'User-Agent': self.USER_AGENT,
            'X-Goog-API-Key': 'AIzaSyAdkSIMNc51XGNEAYWasX9UOWkS5P6sZE4', #Nest website's (public) API key
            'Referer': 'https://home.nest.com',
        }
        request = self.PostMessageWithRetries(url, data, headers=headers, retries=2)
        if request is None:
            return False

        result = request.json()
        self._nest_user_id = result['claims']['subject']['nestId']['id']
        self._nest_access_token = result['jwt']
        self._cache_expiration_text = result['claims']['expirationTime']
        log("Got access token and user id ({}) and remains valid until {}.".format(self._nest_user_id, self._cache_expiration_text))
        return True

    def _GetUser(self):
        if not self._running:
            return False

        url = 'https://home.nest.com/api/0.1/user/' + self._nest_user_id + '/app_launch'
        data = {
            'known_bucket_types': [ 'user' ],
            'known_bucket_versions': [],
        }
        request = self.PostMessageWithRetries(url, data, retries=2)
        if request is None:
            return False

        result = request.json()
        self._transport_url = result['service_urls']['urls']['transport_url']

        for bucket in result['updated_buckets']:
            if bucket['object_key'][:5] == 'user.':
                self._user = bucket['object_key']
                break
        if not self._user:
            self._user = 'user.' + self._nest_user_id

        log("Got user")
        return True

    def UpdateDevices(self):
        try:
            if self.GetNestCredentials():
                return self.GetDevicesAndStatus()
        except Exception as e:
            self._nest_access_error = "Unforseen exception occured in Nest class: {}".format(e)
            log(traceback.format_exc())
        return False

    def GetAccessError(self):
        if not self._nest_access_error:
            return 'All good'
        return self._nest_access_error

    def GetNestCredentials(self):
        #self._ReadCache()
        self._nest_access_error = None

        current_time = datetime.now(pytz.utc).astimezone(tzlocal.get_localzone())
        if self._cache_expiration is not None:
            if (self._cache_expiration-current_time).seconds/60 > max(self._refresh_time, 5):
                return True

        if not self._GetBearerTokenUsingGoogleCookiesIssue_token():
            return False
        if not self._UseBearerTokenToGetAccessTokenAndUserId():
            return False
        if not self._GetUser():
            return False

        try:
            if '.' in self._cache_expiration_text: #there are milliseconds in the text
                format = '%Y-%m-%dT%H:%M:%S.%fZ'
            else:                                  #milliseconds=0, so there are no milliseconds in the text
                format = '%Y-%m-%dT%H:%M:%SZ'
            naive = datetime.strptime(self._cache_expiration_text, format)
        except TypeError:
            # https://stackoverflow.com/questions/40392842/typeerror-in-strptime-in-python-3-4
            naive = datetime.fromtimestamp(time.mktime(time.strptime(self._cache_expiration_text, format)))
        self._cache_expiration = pytz.utc.localize(naive).astimezone(tzlocal.get_localzone())

        #self._WriteCache()
        return True
        
    def GetStatusUserBuckets(self):
        if not self._running:
            return False

        # General Nest information: "structure"
        # Thermostats: "device", "shared",
        # Protect: "topaz"
        # Temperature sensors: "kryptonite"

        url = 'https://home.nest.com/api/0.1/user/' + self._nest_user_id + '/app_launch'
        data = {
            #'known_bucket_types': [ 'buckets', 'delayed_topaz', 'demand_response', 'device', 'device_alert_dialog', 'geofence_info', 'kryptonite', 'link', 'message', 'message_center', 'metadata', 'occupancy', 'quartz', 'safety', 'rcs_settings', 'safety_summary', 'schedule', 'shared', 'structure', 'structure_history', 'structure_metadata', 'topaz', 'topaz_resource', 'track', 'trip', 'tuneups', 'user', 'user_alert_dialog', 'user_settings', 'where', 'widget_track' ],
            'known_bucket_types': [ 'buckets', 'device', 'kryptonite', 'link', 'quartz', 'rcs_settings', 'shared', 'structure', 'topaz', 'user', 'where'],
            'known_bucket_versions': [],
        }
        request = self.PostMessageWithRetries(url, data, retries=2)
        if request is None:
            return False

        self._status = request.json()
        #with open('Nest_dump.txt') as json_file:
        #    self._status = json.load(json_file)        

        #log('Status: {}'.format(json.dumps(self._status, indent=2)))

        try:
            devices = [bucket['value']['swarm'] for bucket in self._status['updated_buckets'] if bucket['object_key'].split('.')[0] == 'structure'][0]
            #Thermostats
            self.device_list = [device.split('.')[1] for device in devices if device.split('.')[0] == 'device']
            #Protects
            self.protect_list = [device.split('.')[1] for device in devices if device.split('.')[0] == 'topaz']
        except:
            pass
            
        return True
            
    def GetDevicesAndStatus(self):
        if not self._running:
            return False
        self.device_list = []
        self.protect_list = []
        status = self.GetStatusUserBuckets()
        log("Got nest devices {}: thermostats {} - protects {}".format(len(self.device_list)+len(self.protect_list),self.device_list, self.protect_list))
        return status

    def GetThermostatInformation(self, device_id):
        info = {}
        try:
            structure_id = [bucket['value']['structure'][10:] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'link.{}'.format(device_id)][0]
            shared = [bucket['value'] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'shared.{}'.format(device_id)][0]
            device = [bucket['value'] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'device.{}'.format(device_id)][0]
            wheres = {where['where_id'] : where['name'] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'where.{}'.format(structure_id) for where in bucket['value']['wheres']}

            info = {
                'Target_temperature': shared['target_temperature'],
                'Current_temperature': shared['current_temperature'],
                'Temperature_scale':  device['temperature_scale'],
                'Humidity': device['current_humidity'],
                'Eco': device['eco']['mode'] != 'schedule', #if not 'schedule' --> in eco-mode
                'Heating': shared['hvac_heater_state'],
                'Target_mode': shared['target_temperature_type'],
                'Target_temperature_low': shared['target_temperature_low'],
                'Target_temperature_high': shared['target_temperature_high'],
                'Where': wheres[device['where_id']],
                'Auto_away': shared['auto_away']
            }
        except:
            pass
            
        return info

    def GetProtectInformation(self, device_id):
        info = {}
        try:
            device = [bucket['value'] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'topaz.{}'.format(device_id)][0]
            structure_id = [bucket['object_key'][10:] for bucket in self._status['updated_buckets'] if (bucket['object_key'].split('.')[0] == 'structure' and 'topaz.{}'.format(device_id) in bucket['value']['swarm'])][0]
            wheres = {where['where_id'] : where['name'] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'where.{}'.format(structure_id) for where in bucket['value']['wheres']}
        
            info = {
                'Smoke_status': device['smoke_status'],
                'Co_status': device['co_status'],
                'Heat_status': device['heat_status'],
                'Serial_number': str(device['serial_number']),
                'Co_previous_peak': str(device['co_previous_peak']),
                'Where': wheres[device['spoken_where_id']] if 'spoken_where_id' in device else wheres[device['where_id']],
                'Battery_low': str(device['battery_health_state']),
                'Battery_level': str(device['battery_level'])
            }
        except:
            pass
            
        return info

    def GetNestInformation(self):
        info = {}
        try:
            structure = [bucket['value'] for bucket in self._status['updated_buckets'] if bucket['object_key'].split('.')[0] == 'structure'][0]

            info = {
                'Name': str(structure['name']),
                'Away': structure['away'],
                'City': structure['city'],
                'Country_code': structure['country_code'],
                'Postal_code': structure['postal_code']
            }

        except:
            pass
            
        return info

    def GetOutsideTempHum(self):
        if not self._running:
            return False
        
        NestInfo = self.GetNestInformation()
        if not ('Postal_code' in NestInfo and 'Country_code' in NestInfo) and (NestInfo['Postal_code'] == '' or NestInfo['Country_code'] == ''):
            return False
            
        url = 'https://home.nest.com/api/0.1/weather/forecast/{},{}'.format(NestInfo['Postal_code'], NestInfo['Country_code'])
        try:
            request = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            request.raise_for_status()
            result = request.json()

            info = {}
            try:
                info = {
                    'City': NestInfo['City'],
                    'Current_humidity': result['now']['current_humidity'],
                    'Current_temperature': result['now']['current_temperature'],
                    'Current_wind': result['now']['current_wind'],
                    'Wind_direction': result['now']['wind_direction']
                }
            except:
                pass
            return info

        except requests.exceptions.Timeout as e:
            self._nest_access_error = 'API request timed out'
        except requests.exceptions.ConnectionError as e:
            self._nest_access_error = 'Connection error API request'
        except requests.exceptions.HTTPError as e:
            self._nest_access_error = 'API request failed (status {})'.format(e.response.status_code)
        except json.JSONDecodeError as e:
            self._nest_access_error = 'Invalid API response'
        return False

    def SetThermostat(self, device_id, mode): #False = set thermostat off
        url = self._transport_url + '/v2/put/shared.' + device_id
        data = {
            'target_change_pending': True,
            'target_temperature_type': mode,
        }
        return self.UpdateNest(url, data, "Thermostat set to {}".format(mode))

    def SetTemperature(self, device_id, target_temperature):
        url = self._transport_url + '/v2/put/shared.' + device_id
        data = {
            'target_change_pending': True,
            'target_temperature': target_temperature,
        }
        return self.UpdateNest(url, data, "Temperature set to {}".format(target_temperature))

    def SetAway(self, device_id, is_away, eco_when_away=True):
        try:
            structure_id = [bucket['value']['structure'][10:] for bucket in self._status['updated_buckets'] if bucket['object_key'] == 'link.{}'.format(device_id)][0]
        except:
            pass
        url = self._transport_url + '/v2/put/structure.' + structure_id
        away_timestamp = datetime.now(pytz.timezone('utc')).astimezone(tzlocal.get_localzone()).timestamp()
        data = {
            'away': is_away,
            'away_timestamp': round(away_timestamp),
            'away_setter': 0,
        }
        if self.UpdateNest(url, data, "Away set to {}".format(is_away)):
            if is_away and eco_when_away:
                self.SetEco(device_id, 'manual-eco')
            return True
        else:
            return False

    def SetEco(self, device_id, mode): # mode equals 'manual-eco' or 'schedule'
        url = self._transport_url + '/v2/put/device.' + device_id
        mode_update_timestamp = datetime.now(pytz.timezone('utc')).astimezone(tzlocal.get_localzone()).timestamp()
        data = {
            'eco': {
                'mode': mode,
                'mode_update_timestamp': round(mode_update_timestamp),
                'touched_by': 4,
            }
        }
        return self.UpdateNest(url, data, "Eco set to mode {}".format(mode))

    def UpdateNest(self, url, data, success_msg, retries=2):
        if self.GetNestCredentials():
            request = self.PostMessageWithRetries(url=url, data=data)
            if request is None:
                return False
            log(success_msg)
            return True
        return False

    def PostMessageWithRetries(self, url, data, headers=None, retries=1):
        if headers == None:
            headers = {
                'X-nl-protocol-version': '1',
                'X-nl-user-id': self._nest_user_id,
                'Authorization': 'Basic ' + self._nest_access_token,
                'Content-type': 'text/json',
                'User-Agent': self.USER_AGENT,
                'Referer': 'https://home.nest.com'
            }

        for i in range(retries):
            if not self._running:
                return None

            if i > 0:
                log("Retry API request")
            try:
                request = requests.post(url=url, json=data, headers=headers, timeout=self.REQUEST_TIMEOUT)
                if request.status_code == 200:
                    self._nest_access_error = None
                    return request
                self._nest_access_error = "API response status code {}".format(request.status_code)
                if request.status_code == 401:
                    log("Status 401 Credentials validation: expiration date/time {} - current date/time {} - delta minutes {}).".format(self._cache_expiration, datetime.now(pytz.utc).astimezone(tzlocal.get_localzone()), (self._cache_expiration-datetime.now(pytz.utc).astimezone(tzlocal.get_localzone())).seconds/60 if self._cache_expiration else None))
                    self._cache_expiration = None
                if request.status_code < 500:
                    break
            except requests.exceptions.Timeout as e:
                self._nest_access_error = 'Connection timeout'

            time.sleep(0.5)

        return None

if __name__ == "__main__":
    import os
    issue_token = os.environ.get('NEST_ISSUE_TOKEN')
    cookie = os.environ.get('NEST_COOKIE')
    if issue_token is None or cookie is None:
        log("Please set environment variables NEST_ISSUE_TOKEN and NEST_COOKIE")
        exit(1)
    thermostat = Nest(issue_token, cookie, 1)
    if thermostat.UpdateDevices():
        infoNest = thermostat.GetNestInformation()
        log("General Nest information: {}".format(infoNest))
        for device in thermostat.device_list:
            info = thermostat.GetThermostatInformation(device)
            log("Nest Thermostat {}: {}".format(device, info))
            log("    Set Temperature: {}".format(thermostat.SetTemperature(device, float(info['Target_temperature']))))
            log("    Set Away: {}".format(thermostat.SetAway(device, infoNest['Away'])))
            log("    Set Ecomode: {}".format(thermostat.SetEco(device, 'manual-eco')))
            log("    Set Thermostat heating: {}".format(thermostat.SetThermostat(device, 'heat')))
        for device in thermostat.protect_list:
            info = thermostat.GetProtectInformation(device)
            log("Nest Protect {}: {}".format(device, info))
        info = thermostat.GetOutsideTempHum()
        log("Weather: {}".format(info))
    log(thermostat.GetAccessError())
