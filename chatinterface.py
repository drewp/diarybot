from __future__ import print_function, division
import logging
from slackclient import SlackClient
from rdflib import ConjunctiveGraph, URIRef, Namespace
from slackbot_priv import db_client, db_secret, db_token, db_refresh
from pprint import pprint
from twisted.internet import reactor, task

DB = Namespace("http://bigasterisk.com/ns/diaryBot#")


class ChatInterface(object):
    def __init__(self, graph, onMsg):
        """
        onMsg(bot, msg)
        """
        self.graph = graph
        self.slack_client = SlackClient(db_token, refresh_token=db_refresh, client_id=db_client, client_secret=db_secret, token_update_callback=self.onUpdate)
        self.slack_client.refresh_access_token()


        ret = self.slack_client.rtm_connect(
            with_team_state=True, auto_reconnect=True)
        if not ret:
            print(ret)
            raise ValueError('rtm_connect')
        task.LoopingCall(self._rtmPoll).start(1)

    def onUpdate(self, *args):
        print('update', args)
    def _rtmPoll(self):
        if not self.slack_client.server.connected:
            raise ValueError('rtm disconnected')
        print(self.slack_client.rtm_read())

    def _getOrCreateChannel(self, name, withUsers):

        ret = self.slack_client.api_call('channels.join', name='healthbot')
        if not ret['ok']:
            print(ret)
            raise ValueError(repr(ret))
        print(ret)
        return ret['channel']['id']
        return

        
        try:
            ret = self.slack_client.api_call(
                'conversations.create',
                name=name,
                is_private=True,
                user_ids=withUsers)
            if not ret['ok']:
                print(ret)
                raise ValueError(repr(ret))
            return ret['channel']['id']
        except ValueError:
            ret = self.slack_client.api_call('groups.list')
            for ch in ret['groups']:
                if ch['name'] == name:
                    if ch['is_archived']:
                        ret = self.slack_client.api_call(
                            'groups.unarchive', channel=ch['id'])
                        if not ret['ok']:
                            raise ValueError(repr(ret))
                    return ch['id']
            raise ValueError("can't create or find %r" % name)

    def _user(self, uri):
        name = self.graph.value(uri, DB['slackUsername']).toPython()

        if not hasattr(self, 'usersListCache'):
            ret = self.slack_client.api_call('users.list')
            if not ret['ok']:
                raise ValueError(repr(ret))
            self.usersListCache = ret

        for u in self.usersListCache['members']:
            if u['name'] == name:
                return u['id']
        raise ValueError('%r not found on slack' % name)

    def sendMsg(self, bot, toUser, msg):
        botName = self.graph.label(bot).toPython()

        imChannel = self._getOrCreateChannel(botName, [self._user(toUser)])

        ret = self.slack_client.api_call(
            "chat.postMessage",
            channel=imChannel,
            text=msg,
            as_user=False,
            username=botName,
        )
        if not ret['ok']:
            raise ValueError(repr(ret))

    def routes(self):
        class SlackEvents(cyclone.web.RequestHandler):
            def post(self, request):
                print("post from slack", request.__dict__)

        return [
            ('/slack/events', SlackEvents),
        ]


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    graph = ConjunctiveGraph()
    graph.parse('bots-secret.n3', format='n3')

    def onMsg(bot, msg):
        print(vars())

    chat = ChatInterface(graph, onMsg)
    chat.sendMsg(DB['healthBot'],
                 URIRef('http://bigasterisk.com/foaf.rdf#drewp'), 'chat test')

    reactor.run()
