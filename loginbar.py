import requests


def getLoginBar(request):
    return requests.get('http://bang:9023/_loginBar',
                        headers={
                            'Cookie':
                            request.headers.get('cookie', ''),
                            'x-site':
                            'http://bigasterisk.com/openidProxySite/diarybot'
                        }).text
