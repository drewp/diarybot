from typing import Dict, Coroutine, Union
import slack
import logging
import aiohttp
from twisted.internet import reactor
from twisted.internet.task import react
from twisted.internet.defer import ensureDeferred, Deferred
from slack.io.aiohttp import SlackAPI
from slack.events import Message
from rdflib import URIRef, Namespace
from pprint import pprint
import asyncio
from twisted.internet import asyncioreactor
asyncioreactor.install(asyncio.get_event_loop())


log = logging.getLogger('chat')

DB = Namespace('http://bigasterisk.com/ns/diaryBot#')
BOT = Namespace('http://bigasterisk.com/bot/')

# see https://meejah.ca/blog/python3-twisted-and-asyncio


def as_future(d: Deferred):
    return d.asFuture(asyncio.get_event_loop())


def as_deferred(f: Union[asyncio.Future, Coroutine]):
    return Deferred.fromFuture(asyncio.ensure_future(f))


class ChatInterface(object):
    def __init__(self, onMsg):
        """handles all bots.

        onMsg(bot, fromUser, msg) -> Deferred
        """
        self.onMsg = onMsg

        self.session = aiohttp.ClientSession()
        self.slack_client = {}
        self._botChannel = {}
        self._userSlackId: Dict[URIRef, str] = {}

    async def initBot(self, bot, token):
        self.slack_client[bot] = SlackAPI(token=token, session=self.session)

        await self._setup(bot)

    def sendMsg(self, bot, toUser, msg):
        return as_deferred(self._sendMsg(bot, toUser, msg))

    async def userIsOnline(self, user: URIRef):
        return True

    async def _channelWithUser(self, bot: URIRef, user: URIRef) -> str:
        userSlackId = await self._slackIdForUser(user)
        async for chan in self.slack_client[bot].iter(slack.methods.CONVERSATIONS_LIST, data={'types': 'im'}):
            if chan['user'] == userSlackId:
                return chan['id']

    async def _sendMsg(self, bot: URIRef, toUser: URIRef, msg: str):
        try:
            try:
                imChannel = await self._channelWithUser(bot, toUser)
            except KeyError:
                log.error(
                    f"no channel between bot {bot.uri} and user {toUser}. Can't send message.")
                return

            post = dict(
                channel=imChannel,
                text=msg,
                as_user=False,
            )
            pprint({'post': post})
            await self.slack_client[bot].query(slack.methods.CHAT_POST_MESSAGE, data=post)
        except Exception:
            log.error('sendMsg failed:')
            import traceback
            traceback.print_exc()
            raise

    async def _setup(self, bot):
        client = self.slack_client[bot]
        log.info('_setup auth.test')
        ret = await client.query(slack.methods.AUTH_TEST)
        user_id = ret['user_id']
        # ret['user'] might actually be mangled like 'healthbot2', so it might
        # differ from bot.name.

        ret = await client.query(slack.methods.USERS_INFO, user=user_id)
        bot_id = ret['user']['profile']['bot_id']

        log.info(f'{bot} rtm starts')
        async for event in client.rtm():
            log.info(f'{bot} got event {event}')
            if isinstance(event, Message):
                log.info(f'got message {event:r}')
                await as_future(self.onMsg(bot,
                                           self._userFromSlack(event['user']),
                                           event['text']))
            else:
                pprint(event)
        log.error(f'rtm stopped for bot {bot}')

    def _userFromSlack(self, slackUser) -> URIRef:
        for u, i in self._userSlackId.items():
            if i == slackUser:
                return u
        raise ValueError(f'unknown user {slackUser}')

    async def _slackIdForUser(self, user: URIRef) -> str:
        if user in self._userSlackId:
            return self._userSlackId[user]

        anyClient = next(iter(self.slack_client.values()))
        async for member in anyClient.iter(slack.methods.USERS_LIST):
            userUriForSlackName = {
                'kelsi': URIRef('http://bigasterisk.com/kelsi/foaf.rdf#kelsi'),
                'drew': URIRef('http://bigasterisk.com/foaf.rdf#drewp'),
            }
            if member['name'] in userUriForSlackName:
                self._userSlackId[userUriForSlackName[member['name']]
                                  ] = member['id']

        return self._userSlackId[user]


async def _main(reactor):

    def onMsg(bot, user, msg):
        print(vars())
        reactor.callLater(float(msg),
                          lambda: as_deferred(chat.sendMsg(bot,
                                                           URIRef(
                                                               'http://bigasterisk.com/foaf.rdf#drewp'),
                                                           'echo from %s' % bot)))

    chat = ChatInterface(onMsg)
    # await chat.sendMsg(BOT['houseBot'],
    # URIRef('http://bigasterisk.com/foaf.rdf#drewp'), 'chat test')
    await Deferred()


def main():
    logging.basicConfig(level=logging.DEBUG)

    def onMsg(bot, user, msg):
        print(vars())
        reactor.callLater(float(msg),
                          lambda: as_deferred(chat.sendMsg(bot,
                                                           URIRef(
                                                               'http://bigasterisk.com/foaf.rdf#drewp'),
                                                           'echo from %s' % bot)))

    chat = ChatInterface(onMsg)
    reactor.run()


if __name__ == '__main__':
    main()
