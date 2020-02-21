# Must come first to set twisted's default reactor.
import asyncio
from twisted.internet import asyncioreactor, defer
asyncioreactor.install(asyncio.get_event_loop())

from typing import Dict, Coroutine, Union, Any, Callable
import slack
import logging
import aiohttp
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from slack.io.aiohttp import SlackAPI
from slack.events import Message
from rdflib import URIRef
from pprint import pprint

log = logging.getLogger('chat')
BotType = Any  # workaround for cycle?
# see https://meejah.ca/blog/python3-twisted-and-asyncio


def as_future(d: Deferred):
    return d.asFuture(asyncio.get_event_loop())


def as_deferred(f: Union[asyncio.Future, Coroutine]):
    return Deferred.fromFuture(asyncio.ensure_future(f))


class ChatInterface(object):
    def __init__(self, onMsg: Callable):
        """handles all bots. Public APIs return deferreds.

        onMsg(bot, fromUser, msg) -> Deferred
        """
        self.onMsg = onMsg

        self.session = aiohttp.ClientSession()
        self.slack_client: Dict[BotType, SlackAPI] = {}
        self._botChannel = {}
        self._userSlackId: Dict[URIRef, str] = {}
        self._slackUserUri: Dict[str, URIRef] = {}

    def initBot(self, bot: BotType, token: str) -> Deferred:
        self.slack_client[bot] = SlackAPI(token=token, session=self.session)

        return as_deferred(self._setup(bot))

    async def _setup(self, bot: BotType) -> None:
        client = self.slack_client[bot]
        log.info('_setup auth.test')
        ret = await client.query(slack.methods.AUTH_TEST)
        user_id = ret['user_id']
        # ret['user'] might actually be mangled like 'healthbot2', so it might
        # differ from bot.name.

        ret = await client.query(slack.methods.USERS_INFO,
                                 data={'user': user_id})
        bot_id = ret['user']['profile']['bot_id']

        log.info(f'{bot} rtm starts')
        async for event in client.rtm():
            log.info(f'{bot} got event {event}')
            if isinstance(event, Message):
                await self._onMsg(bot, event)
            else:
                log.info(f'unhandled event {event}')
        log.error(f'rtm stopped for bot {bot}')

    async def _onMsg(self, bot: BotType, event):
        log.info(f'got message {event}')
        await as_future(
            self.onMsg(bot, await self._userFromSlackId(event['user']),
                       event['text']))

    def sendMsg(self, bot: BotType, toUser: URIRef, msg: str) -> Deferred:
        return as_deferred(self._sendMsg(bot, toUser, msg))

    async def _sendMsg(self, bot: URIRef, toUser: URIRef,
                       msg: str) -> None:
        try:
            try:
                imChannel = await self._channelWithUser(bot, toUser)
            except KeyError as e:
                log.error(f'{e!r}; skipping send')
                return

            post = dict(
                channel=imChannel,
                text=msg,
                as_user=False,
            )
            pprint({'post': post})
            await self.slack_client[bot].query(slack.methods.CHAT_POST_MESSAGE,
                                               data=post)
        except Exception:
            log.error('sendMsg failed:')
            import traceback
            traceback.print_exc()
            raise

    async def userIsOnline(self, user: URIRef):
        return True

    def anyClient(self) -> SlackAPI:
        if not self.slack_client:
            raise ValueError("no slack clients")
        return next(iter(self.slack_client.values()))

    async def _channelWithUser(self, bot: URIRef, user: URIRef) -> str:
        userSlackId = await self._slackIdForUser(user)
        async for chan in self.slack_client[bot].iter(
                slack.methods.CONVERSATIONS_LIST, data={'types': 'im'}):
            if chan['user'] == userSlackId:
                return chan['id']
        raise ValueError(
            f'no channel between bot {bot.uri} and user {userSlackId!r}')

    async def _readUserList(self):
        client = self.anyClient()
        async for member in client.iter(slack.methods.USERS_LIST):
            log.info(
                f'slack user {member["id"]!r} has name {member["name"]!r}')
            userUriForSlackName = {
                'kelsi': URIRef('http://bigasterisk.com/kelsi/foaf.rdf#kelsi'),
                'drew': URIRef('http://bigasterisk.com/foaf.rdf#drewp'),
            }
            if member['name'] in userUriForSlackName:
                uri = userUriForSlackName[member['name']]
                self._userSlackId[uri] = member['id']
                self._slackUserUri[member['id']] = uri

    async def _slackIdForUser(self, user: URIRef) -> str:
        if user in self._userSlackId:
            return self._userSlackId[user]

        await self._readUserList()
        return self._userSlackId[user]

    async def _userFromSlackId(self, slackUser: str) -> URIRef:
        if slackUser in self._slackUserUri:
            return self._slackUserUri[slackUser]
        await self._readUserList()
        return self._slackUserUri[slackUser]

class NoChat:
    def initBot(self, bot: BotType, token: str) -> Deferred:
        return defer.succeed(None)
    def sendMsg(self, bot: BotType, toUser: URIRef, msg: str) -> Deferred:
        return defer.succeed(None)
    async def userIsOnline(self, user: URIRef):
        return False
    def anyClient(self) -> SlackAPI:
        raise ValueError("no chat")


async def _main(reactor):
    def onMsg(bot, user, msg):
        reactor.callLater(
            float(msg), lambda: as_deferred(
                chat.sendMsg(bot,
                             URIRef('http://bigasterisk.com/foaf.rdf#drewp'),
                             'echo from %s' % bot)))

    chat = ChatInterface(onMsg)
    # await chat.sendMsg(BOT['houseBot'],
    # URIRef('http://bigasterisk.com/foaf.rdf#drewp'), 'chat test')
    await Deferred()


def main():
    logging.basicConfig(level=logging.DEBUG)

    def onMsg(bot, user, msg):
        print(vars())
        reactor.callLater(
            float(msg), lambda: as_deferred(
                chat.sendMsg(bot,
                             URIRef('http://bigasterisk.com/foaf.rdf#drewp'),
                             'echo from %s' % bot)))

    chat = ChatInterface(onMsg)
    reactor.run()


if __name__ == '__main__':
    main()
