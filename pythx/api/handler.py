import logging
import urllib.parse
from typing import Dict, List

import requests

from pythx import config
from pythx.middleware.base import BaseMiddleware
from pythx.models.exceptions import PythXAPIError


def print_request(req) -> str:
    """Generate a pretty-printed HTTP request string.

    :param req: The prepared requests HTTP request
    :return: Pretty HTTP request string
    """
    return "\nHTTP/1.1 {method} {url}\n{headers}\n\n{body}\n".format(
        method=req.method,
        url=req.url,
        headers="\n".join("{}: {}".format(k, v) for k, v in req.headers.items()),
        body=req.body.decode(),
    )


def print_response(res):
    """Generate a pretty-printed HTTP response string.

    :param res: The received requests HTTP response
    :return: Pretty HTTP response string
    """
    return "\nHTTP/1.1 {status_code}\n{headers}\n\n{body}\n".format(
        status_code=res.status_code,
        headers="\n".join("{}: {}".format(k, v) for k, v in res.headers.items()),
        body=res.content.decode(),
    )


LOGGER = logging.getLogger(__name__)


class APIHandler:
    """Handle the low-level API interaction.

    The API handler takes care of serializing API requests, sending them to the configured
    endpoint, parsing the response into its respective domain model, as well as registering
    and executing request/response middlewares.
    """

    def __init__(self, middlewares: List[BaseMiddleware] = None, staging: bool = False):
        middlewares = middlewares if middlewares is not None else []
        self.middlewares = middlewares
        self.mode = "staging" if staging else "production"

    @staticmethod
    def send_request(request_data: Dict, auth_header: Dict[str, str] = None):
        """Send a request to the API.

        This method takes a data dictionary holding the request's method (HTTP verb),
        any additional headers, the URL to send the request to, its payload, and any
        URL parameters it requires. This dictionary is generated by the
        APIHandler.assemble_request method.

        An example for getting the detected issues for an analysis job's UUID:

        .. code-block:: python3

            {
                "method": "GET",
                "headers": {},
                "url": "https://api.mythx.io/v1/analyses/6b9e4a52-f061-4960-8246-e1560627336a/issues",
                "payload": "",
                "params": {}
            }

        If the action requires authentication, the auth headers are passed in a separate, optional
        parameter. It holds the user's JWT access token.

        If the request fails (returns a non 200 status code), a PythXAPIError is raised.

        :param request_data: The request data dictionary
        :param auth_header: The authorization header carrying the access token
        :return: The raw response payload string
        """
        if auth_header is None:
            auth_header = {}
        method = request_data["method"].upper()
        headers = request_data["headers"]
        headers.update(auth_header)
        url = request_data["url"]
        payload = request_data["payload"]
        params = request_data["params"]
        response = requests.request(
            method=method, url=url, headers=headers, json=payload, params=params
        )
        LOGGER.debug(print_request(response.request))
        LOGGER.debug(print_response(response))
        if response.status_code != 200:
            raise PythXAPIError(
                "Got unexpected status code {}: {}".format(
                    response.status_code, response.content.decode()
                )
            )
        return response.text

    def execute_request_middlewares(self, req):
        """Sequentially execute the registered request middlewares.

        Each middleware gets the request's data dictionary as generated by the
        APIHandler.assemble_request method. On top of the request any manipulations can
        be made.

        It is worth mentioning here that this is a simple loop iterating over the middleware
        list, calling each middleware's :code:`process_request` method. It is expected that
        each registered middleware exposes this method and returns a data dictionary in the
        same format as the one passed in. It also means that the order in which middlewares
        are registered can matter, even though it is recommended that middlewares are kept
        associative in nature.

        :param req: The request's data dictionary
        :return: The updated data dict - ready to be sent to the API
        """
        for mw in self.middlewares:
            LOGGER.debug("Executing request middleware: %s", mw)
            req = mw.process_request(req)
        return req

    def execute_response_middlewares(self, resp):
        """Sequentially execute the registered response middlewares.

        Each middleware gets the serialized response domain model. On top of the request any
        manipulations can be made. Furthermode, each domain model's helper methods can be
        used.

        It is worth mentioning here that this is a simple loop iterating over the middleware
        list, calling each middleware's :code:`process_response` method. It is expected that
        each registered middleware exposes this method and returns a domain model of the
        same type as the one passed in. It also means that the order in which middlewares
        are registered can matter, even though it is recommended that middlewares are kept
        associative in nature.

        :param resp: The response domain model
        :return: The updated response domain model - ready to be passed on to the user
        """
        for mw in self.middlewares:
            LOGGER.debug("Executing response middleware: %s", mw)
            resp = mw.process_response(resp)
        return resp

    def parse_response(self, resp: str, model):
        """Parse the API response into its respective domain model variant.

        This method takes the raw HTTP response and a class it should deserialize the responsse
        data into. As each domain model implements the :code:`from_json` method, we simply call
        it on the raw input data and return the resulting model.

        If a deserialization or validation error is raised, it is not caught and directly passed
        on to the user.

        :param resp: The raw HTTP response JSON payload
        :param model: The domain model class the data should be deserialized into
        :return: The domain model holding the response data
        """
        m = model.from_json(resp)
        return self.execute_response_middlewares(m)

    def assemble_request(self, req):
        """Assemble a request that is later sent to the API.

        This method generates an intermediate data dictionary format holding all the relevant
        request data needed by the API. This encompasses the HTTP verb, the request payload
        content (if there is any), the request's URL parameters, additional headers, as well as
        the API endpoint the request should be sent to.

        Each of these data points is encoded in the domain model as a property. The endpoint
        URL is constructed from the domain model's path (e.g. :code:`/v1/auth/login`) and the
        API base path (e.g. :code:`https://staging.api.mythx.io` for the staging deployment),
        which is contained in the library configuration module.

        Before the serialized request is returned, all registered middlewares are applied to it.

        :param req: The request domain model
        :return: The serialized request with all middlewares applied
        """
        url = urllib.parse.urljoin(config["endpoints"][self.mode], req.endpoint)
        base_request = {
            "method": req.method,
            "payload": req.payload,
            "params": req.parameters,
            "headers": req.headers,
            "url": url,
        }
        return self.execute_request_middlewares(base_request)
