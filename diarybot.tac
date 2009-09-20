from twisted.application import service
from diarybot import makeBots

application = service.Application("diarybot")
makeBots(application, "bots.n3")
