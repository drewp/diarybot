# why did this break around when I went to py3?

from typing import Dict, List
import cyclone
import urllib
import re

class FixRequestHandler(cyclone.web.RequestHandler):
    def get_arguments(self, name: str, strip=True) -> List[str]:
        # consider postbody form and query params
        values = []
        queryArgs: Dict[bytes, List[bytes]] = self.request.arguments

        bodyArgs: Dict[bytes, List[bytes]] = dict((k, [v])
                                                  for k, v in urllib.parse.parse_qsl(self.request.body))

        for v in queryArgs.get(name.encode('utf8'), []) or bodyArgs.get(name.encode('utf8'), []):
            v = self.decode_argument(v, name=name)
            if isinstance(v, str):
                # Get rid of any weird control chars (unless decoding gave
                # us bytes, in which case leave it alone)
                v = re.sub(r"[\x00-\x08\x0e-\x1f]", " ", v)
            if strip:
                v = v.strip()
            values.append(v)
        return values
