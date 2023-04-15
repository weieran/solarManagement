import logging
import os
from enum import Enum
import requests
import sys
from datetime import datetime, timedelta


class InvalidTokenException(Exception):
    """Raised when the received access token is invalid """
    pass


class InvalidGeoLocationException(Exception):
    """Raised when the received access token is invalid """
    pass


class InvalidWeatherException(Exception):
    """Raised when the received access token is invalid """
    pass


class Weather:
    TOKEN_VALIDITY_DAY = timedelta(days=7)

    def __init__(self, client_id: str, client_secret: str, location: str):
        self.logger = logging.getLogger(__name__)
        self.client_id = client_id
        self.client_secret = client_secret
        self.last_header_update = None
        self.headers = None
        self.geo_location_id = self.get_geo_location_id(location)

    def _get_headers(self):
        if self.last_header_update is None or self.last_header_update + self.TOKEN_VALIDITY_DAY < datetime.now():
            self.logger.debug("Updating token, as it is older than 7 days")
            self.last_header_update = datetime.now()
            access_token = self.get_access_token()
            self.headers = {'Authorization': f"Bearer {access_token}"}
        return self.headers

    def get_access_token(self):
        # Define the authentication API endpoint URL and parameters
        url = "https://api.srgssr.ch/oauth/v1/accesstoken"
        params = {
            "grant_type": "client_credentials"
        }

        # Send a POST request to the authentication API endpoint
        response = requests.post(url, params=params, auth=(self.client_id, self.client_secret))
        if not response.ok:
            self.logger.error(f"Invalid token, response: {response.status_code}  ")
            raise InvalidTokenException

        # Parse the JSON response data
        data = response.json()

        # Extract the access token from the response data
        return data["access_token"]

    def get_geo_location_id(self, location: str):
        # Define the API endpoint URL and parameters
        url = "https://api.srgssr.ch/srf-meteo/geolocationNames"
        params = {"name": location}

        # Send a GET request to the API endpoint
        response = requests.get(url, params=params, headers=self._get_headers())

        if response.status_code != 200:
            self.logger.error(f"Invalid geolocation, response: {response.status_code}  ")
            raise InvalidGeoLocationException

        # Parse the JSON response data
        data = response.json()

        # Extract the location ID from the response data
        geo_location_id = data[0]["geolocation"]["id"]
        return geo_location_id

    class ForecastDuration(Enum):
        minutes60 = "60minutes"
        hour = "hour"
        day = "day"

    def get_weather_forecast(self, forcast_duration: ForecastDuration):
        # Define the API endpoint URL and parameters
        url = f"https://api.srgssr.ch/srf-meteo/forecast/{self.geo_location_id}"
        print(url)
        params = {"type": forcast_duration.value}

        # Send a GET request to the API endpoint
        response = requests.get(url, params=params, headers=self._get_headers())

        if response.status_code != 200:
            self.logger.error(f"Invalid weather, response: {response.status_code}  ")
            raise InvalidWeatherException

        # Parse the JSON response data
        data = response.json()

        # Extract the forecast data from the response data
        forecast = data["forecast"]

        # Print the forecast data
        print(forecast)


if __name__ == '__main__':
    srf_client_id = os.environ.get("SRF_METEO_CLIENT_ID")
    srf_client_secret = os.environ.get("SRF_METEO_CLIENT_SECRET")

    sachseln = Weather(srf_client_id, srf_client_secret, "Sachseln")
    weather = sachseln.get_weather_forecast(Weather.ForecastDuration.hour)
    print(weather)
    sys.exit()
