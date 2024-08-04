import asyncio
import json
import random
from time import time
from random import randint
from urllib.parse import unquote
import traceback
from datetime import datetime

import aiohttp
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from pyrogram import Client
from pyrogram.errors import Unauthorized, UserDeactivated, AuthKeyUnregistered
from pyrogram.raw.functions.messages import RequestWebView

from bot.config import settings
from bot.core.Bypass import CustomTLSContext
from bot.utils import logger
from bot.utils.graphql import Query, OperationName
from bot.utils.boosts import FreeBoostType, UpgradableBoostType
from bot.exceptions import InvalidSession
from .headers import headers
from .useragents import user_agents

class Tapper:
    def __init__(self, tg_client: Client):
        self.session_name = tg_client.name
        self.tg_client = tg_client
        self.session_dict = self.load_session_data()

        self.GRAPHQL_URL = 'https://api-gw-tg.memefi.club/graphql'
        if settings.AUTO_GENERATE_USER_AGENT_FOR_EACH_SESSION == True:
            headers['User-Agent'] = self.get_user_agent()
        else:
            headers['User-Agent'] = user_agents[0]

    def get_random_user_agent(self):
        """Returns a random user agent from the list."""
        return random.choice(user_agents)

    # Function to save session data to JSON file
    def save_session_data(self, session_dict):
        with open('session_user_agents.json', 'w') as file:
            json.dump(session_dict, file, indent=4)

    # Function to load session data from JSON file
    def load_session_data(self):
        try:
            with open('session_user_agents.json', 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            return {}
        
    def get_user_agent(self):
        """Returns the user agent for a given session ID.
        If no user agent is assigned to the session ID, assigns a new random one."""
        if self.session_name in self.session_dict:
            return self.session_dict[self.session_name]

        # Generate a random user agent and check if it's already assigned to another session
        logger.info(f"{self.session_name} | Generating new user agent...")
        new_user_agent = self.get_random_user_agent()
        while any(new_user_agent == agent for agent in self.session_dict.values()):
            new_user_agent = self.get_random_user_agent()

        self.session_dict[self.session_name] = new_user_agent
        self.save_session_data(self.session_dict)
        return new_user_agent
        
    async def get_tg_web_data(self, proxy: str | None):
        if proxy:
            proxy = Proxy.from_str(proxy)
            proxy_dict = dict(
                scheme=proxy.protocol,
                hostname=proxy.host,
                port=proxy.port,
                username=proxy.login,
                password=proxy.password
            )
        else:
            proxy_dict = None

        self.tg_client.proxy = proxy_dict

        try:
            if not self.tg_client.is_connected:
                try:
                    await self.tg_client.connect()
                except (Unauthorized, UserDeactivated, AuthKeyUnregistered):
                    raise InvalidSession(self.session_name)

            web_view = await self.tg_client.invoke(RequestWebView(
                peer=await self.tg_client.resolve_peer('memefi_coin_bot'),
                bot=await self.tg_client.resolve_peer('memefi_coin_bot'),
                platform='android',
                from_bot_menu=False,
                url='https://tg-app.memefi.club/game'
            ))

            auth_url = web_view.url
            tg_web_data = unquote(
                string=unquote(
                    string=auth_url.split('tgWebAppData=', maxsplit=1)[1].split('&tgWebAppVersion', maxsplit=1)[0]))

            query_id = tg_web_data.split('query_id=', maxsplit=1)[1].split('&user', maxsplit=1)[0]
            user_data = tg_web_data.split('user=', maxsplit=1)[1].split('&auth_date', maxsplit=1)[0]
            auth_date = tg_web_data.split('auth_date=', maxsplit=1)[1].split('&hash', maxsplit=1)[0]
            hash_ = tg_web_data.split('hash=', maxsplit=1)[1]

            me = await self.tg_client.get_me()

            json_data = {
                'operationName': OperationName.MutationTelegramUserLogin,
                'query': Query.MutationTelegramUserLogin,
                'variables': {
                    'webAppData': {
                        'auth_date': int(auth_date),
                        'hash': hash_,
                        'query_id': query_id,
                        'checkDataString': f'auth_date={auth_date}\nquery_id={query_id}\nuser={user_data}',
                        'user': {
                            'id': me.id,
                            'allows_write_to_pm': True,
                            'first_name': me.first_name,
                            'last_name': me.last_name if me.last_name else '',
                            'username': me.username if me.username else '',
                            'language_code': me.language_code if me.language_code else 'en',
                            'platform': 'ios',
                            'version': '7.2'
                        },
                    },
                }
            }

            if self.tg_client.is_connected:
                await self.tg_client.disconnect()

            return json_data

        except InvalidSession as error:
            raise error

        except Exception as error:
            logger.error(f"{self.session_name} | ❗️Unknown error during Authorization: {error}")
            await asyncio.sleep(delay=3)

    async def get_access_token(self, http_client: aiohttp.ClientSession, tg_web_data: dict[str]):
        try:
            response = await http_client.post(url=self.GRAPHQL_URL, json=tg_web_data)
            response.raise_for_status()

            response_json = await response.json()
            access_token = response_json['data']['telegramUserLogin']['access_token']

            return access_token
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while getting Access Token: {error}")
                await asyncio.sleep(delay=3)

    async def get_profile_data(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.QUERY_GAME_CONFIG,
                'query': Query.QUERY_GAME_CONFIG,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()
            profile_data = response_json['data']['telegramGameGetConfig']
            
            return profile_data
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while getting Profile Data: {error}")
                await asyncio.sleep(delay=3)

    async def get_user_data(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.QueryTelegramUserMe,
                'query': Query.QueryTelegramUserMe,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()
            user_data = response_json['data']['telegramUserMe']
            

            return user_data
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while getting User Data: {error}")
                await asyncio.sleep(delay=3)

    async def set_next_boss(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.telegramGameSetNextBoss,
                'query': Query.telegramGameSetNextBoss,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            return True
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while Setting Next Boss: {error}")
                await asyncio.sleep(delay=3)

            return False
        
    async def get_bot_config(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.TapbotConfig,
                'query': Query.TapbotConfig,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()
            bot_config = response_json['data']['telegramGameTapbotGetConfig']
            return bot_config
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while getting Bot Config: {error}")
                await asyncio.sleep(delay=3)
    
    async def start_bot(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.TapbotStart,
                'query': Query.TapbotStart,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            return True
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while Starting Bot: {error}")
                await asyncio.sleep(delay=3)

            return False
    
    async def claim_bot(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.TapbotClaim,
                'query': Query.TapbotClaim,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()
            response_json = await response.json()
            if response_json is None:
                logger.error("Response JSON is None")
                return None

            data = response_json.get('data')
            
            if data is None:
                logger.error("'data' key not found or is None in the response JSON")
                return None

            tapbotClaim = data.get("telegramGameTapbotClaimCoins")
            
            if tapbotClaim is None:
                logger.error("'telegramGameTapbotClaimCoins' key not found or is None in 'data'")
                return None

            return  {"isClaimed": False, "data": tapbotClaim}
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                    return {"isClaimed": True, "data": None}
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
                    return {"isClaimed": True, "data": None}
            else:
                return {"isClaimed": True, "data": None}
        
    async def spin_game(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.Spinner,
                'query': Query.Spinner,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()
            return response_json["data"]
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while Claiming Referral Bonus: {error}")
                await asyncio.sleep(delay=3)

            return False
        
    async def claim_referral_bonus(self, http_client: aiohttp.ClientSession):
        try:
            json_data = {
                'operationName': OperationName.Mutation,
                'query': Query.Mutation,
                'variables': {}
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            return True
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while Spinning: {error}")
                await asyncio.sleep(delay=3)

            return False

    async def apply_boost(self, http_client: aiohttp.ClientSession, boost_type: FreeBoostType):
        try:
            json_data = {
                'operationName': OperationName.telegramGameActivateBooster,
                'query': Query.telegramGameActivateBooster,
                'variables': {
                    'boosterType': boost_type
                }
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            return True
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | ❗️Unknown error while Apply {boost_type} Boost: {error}")
                await asyncio.sleep(delay=3)

            return False

    async def upgrade_boost(self, http_client: aiohttp.ClientSession, boost_type: UpgradableBoostType):
        try:
            json_data = {
                'operationName': OperationName.telegramGamePurchaseUpgrade,
                'query': Query.telegramGamePurchaseUpgrade,
                'variables': {
                    'upgradeType': boost_type
                }
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            return True
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                return False

    async def send_taps(self, http_client: aiohttp.ClientSession, nonce: str, taps: int):
        try:
            vectorArray = []
            for tap in range(taps):
                """ check if tap is greater than 4 or less than 1 and set tap to random number between 1 and 4"""
                if tap > 4 or tap < 1:
                    tap = randint(1, 4)
                vectorArray.append(tap)

            vector = ",".join(str(x) for x in vectorArray)            
            json_data = {
                'operationName': OperationName.MutationGameProcessTapsBatch,
                'query': Query.MutationGameProcessTapsBatch,
                'variables': {
                    'payload': {
                        'nonce': nonce,
                        'tapsCount': taps,
                        'vector': vector
                    },
                }
            }

            response = await http_client.post(url=self.GRAPHQL_URL, json=json_data)
            response.raise_for_status()

            response_json = await response.json()
            profile_data = response_json['data']['telegramGameProcessTapsBatch']
            return profile_data
        except aiohttp.ClientResponseError as error:
            if error.status == 429:
                if 'Retry-After' in error.headers:
                    retry_after = int(error.headers['Retry-After'])
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logger.error(f"{self.session_name} | Too many requests. Sleeping for 60 seconds...")
                    await asyncio.sleep(60)  # Wait for 60 seconds
            else:
                logger.error(f"{self.session_name} | Unknown error when Tapping: {error}")
                await asyncio.sleep(10)

    async def check_proxy(self, http_client: aiohttp.ClientSession, proxy: Proxy) -> None:
        try:
            response = await http_client.get(url='https://api.ipify.org?format=json', timeout=aiohttp.ClientTimeout(10))
            ip = (await response.json()).get('ip')
            logger.info(f"{self.session_name} | Proxy IP: {ip}")
        except aiohttp.ClientResponseError as error:
            logger.error(f"{self.session_name} | Proxy: {proxy} | Error: {error}")

    async def run(self, proxy: str | None):
        access_token_created_time = 0
        turbo_time = 0
        active_turbo = False
        noBalance = False
       
        context = CustomTLSContext.create_custom_ssl_context()
        connector = ProxyConnector().from_url(url=proxy, rdns=True, ssl=context) if proxy \
            else aiohttp.TCPConnector(ssl=context)

        async with aiohttp.ClientSession(headers=headers, connector=connector) as http_client:
            if proxy:
                await self.check_proxy(http_client=http_client, proxy=proxy)

            while True:
                noBalance = False
                try:
                    if time() - access_token_created_time >= 3600:
                        tg_web_data = await self.get_tg_web_data(proxy=proxy)
                        access_token = await self.get_access_token(http_client=http_client, tg_web_data=tg_web_data)

                        http_client.headers["Authorization"] = f"Bearer {access_token}"
                        headers["Authorization"] = f"Bearer {access_token}"

                        access_token_created_time = time()

                        profile_data = await self.get_profile_data(http_client=http_client)

                        balance = profile_data['coinsAmount']

                        nonce = profile_data['nonce']

                        current_boss = profile_data['currentBoss']
                        current_boss_level = current_boss['level']
                        boss_max_health = current_boss['maxHealth']
                        boss_current_health = current_boss['currentHealth']

                        logger.info(f"{self.session_name} | Current boss level: <m>{current_boss_level}</m> | "
                                    f"Boss health: <e>{boss_current_health}</e> out of <r>{boss_max_health}</r>")

                        await asyncio.sleep(delay=.5)


                    taps = randint(a=settings.RANDOM_TAPS_COUNT[0], b=settings.RANDOM_TAPS_COUNT[1])
                    bot_config = await self.get_bot_config(http_client=http_client)
                    telegramMe = await self.get_user_data(http_client=http_client)

                    profile_data = await self.get_profile_data(http_client=http_client)

                    if not profile_data:
                        continue

                    available_energy = profile_data['currentEnergy']

                    free_boosts = profile_data['freeBoosts']
                    turbo_boost_count = free_boosts['currentTurboAmount']
                    energy_boost_count = free_boosts['currentRefillEnergyAmount']

                    next_tap_level = profile_data['weaponLevel'] + 1
                    next_energy_level = profile_data['energyLimitLevel'] + 1
                    next_charge_level = profile_data['energyRechargeLevel'] + 1

                    nonce = profile_data['nonce']

                    current_boss = profile_data['currentBoss']
                    current_boss_level = current_boss['level']
                    boss_current_health = current_boss['currentHealth']
                    min_energy = taps * profile_data['weaponLevel']
                    balance = profile_data['coinsAmount']
                    spinEnergyTotal = profile_data["spinEnergyTotal"]
                    rewardAmount = 0
                    rewardType = ""

                    if spinEnergyTotal > 0 and settings.AUTO_SPIN is True:
                        game_result = await self.spin_game(http_client=http_client)
                        if game_result:
                            rewardAmount = game_result["slotMachineSpin"]["rewardAmount"]
                            rewardType = game_result["slotMachineSpin"]["rewardType"]
                            profile_data = await self.get_profile_data(http_client=http_client)
                            spinEnergyTotal = profile_data["spinEnergyTotal"]
                            balance = profile_data["coinsAmount"]
                            logger.info(f"{self.session_name} | 🔥Reward Amount: <c>{rewardAmount}</c> | Reward Type: <m>{rewardType}</m> | Available Spin: <e>{spinEnergyTotal}</e>")

                            await asyncio.sleep(delay=1)

                    if min_energy >= available_energy:
                        logger.warning(f"{self.session_name} | Not enough energy to send {taps} taps. "
                                       f"Needed <le>{min_energy+1}</le> energy to send taps"
                                       f" | Available: <ly>{available_energy}</ly>")
                        if (energy_boost_count > 0
                            and settings.APPLY_DAILY_ENERGY is True):
                            logger.info(f"{self.session_name} | 😴 Sleep 5s before activating the daily energy boost")
                            await asyncio.sleep(delay=5)

                            status = await self.apply_boost(http_client=http_client, boost_type=FreeBoostType.ENERGY)
                            if status is True:
                                logger.success(f"{self.session_name} | 👉 Energy boost applied")

                                await asyncio.sleep(delay=1)

                            continue


                        logger.info("Sleep 50s")
                        await asyncio.sleep(delay=50)

                        profile_data = await self.get_profile_data(http_client=http_client)

                        continue

                    if active_turbo:
                        taps += settings.ADD_TAPS_ON_TURBO
                        if time() - turbo_time > 10:
                            active_turbo = False
                            turbo_time = 0

                    profile_data = await self.send_taps(http_client=http_client, nonce=nonce, taps=taps)
                    new_balance = profile_data['coinsAmount']
                    calc_taps = new_balance - balance
                    balance = new_balance

                    if telegramMe['isReferralInitialJoinBonusAvailable'] is True:
                        await self.claim_referral_bonus(http_client=http_client)
                        logger.info(f"{self.session_name} | 🔥Referral bonus was claimed")
                    
                    if bot_config['isPurchased'] is False and settings.AUTO_BUY_TAPBOT is True:
                        if  balance >= 200000:
                            await self.upgrade_boost(http_client=http_client, boost_type=UpgradableBoostType.TAPBOT)
                            logger.info(f"{self.session_name} | 👉 Tapbot was purchased - 😴 Sleep 3s")
                            await asyncio.sleep(delay=3)
                            bot_config = await self.get_bot_config(http_client=http_client)
                        else:
                            logger.info(f"{self.session_name} | 👉 Tapbot wasn't purchased due to insufficient balance - 😴 Sleep 3s")
                            await asyncio.sleep(delay=3)
                            bot_config = await self.get_bot_config(http_client=http_client)
                    if bot_config['isPurchased'] is True:
                        if bot_config['usedAttempts'] < bot_config['totalAttempts'] and bot_config['endsAt'] is None:
                            await self.start_bot(http_client=http_client)
                            bot_config = await self.get_bot_config(http_client=http_client)
                            logger.info(f"{self.session_name} | 👉 Tapbot is started")

                        else:
                            if bot_config['endsAt'] is not None:
                                current_datetime_utc = datetime.now().astimezone()
                                given_datetime_str = bot_config['endsAt']
                                given_datetime = datetime.fromisoformat(given_datetime_str.replace("Z", "+00:00"))

                                if(given_datetime <= current_datetime_utc):
                                    tapbotClaim = await self.claim_bot(http_client=http_client)
                                    if tapbotClaim['isClaimed'] == False and tapbotClaim['data']:
                                        logger.info(f"{self.session_name} | 👉 Tapbot was claimed - 😴 Sleep 5s before starting again")
                                        await asyncio.sleep(delay=3)
                                        bot_config = tapbotClaim['data']
                                        await asyncio.sleep(delay=2)

                                        if bot_config['usedAttempts'] < bot_config['totalAttempts']:
                                            await self.start_bot(http_client=http_client)
                                            logger.info(f"{self.session_name} | 👉 Tapbot is started - 😴 Sleep 5s")
                                            await asyncio.sleep(delay=5)
                                            bot_config = await self.get_bot_config(http_client=http_client)
                    
                    if calc_taps > 0:
                        logger.success(f"{self.session_name} | ✅ Successful tapped! 🔨 | "
                                    f"Balance: <c>{balance}</c> (<g>+{calc_taps} 😊</g>) | "
                                    f"Boss health: <e>{boss_current_health}</e>")
                    else:
                        noBalance = True

                    if boss_current_health <= 0:
                        logger.info(f"{self.session_name} | 👉 Setting next boss: <m>{current_boss_level+1}</m> lvl")

                        status = await self.set_next_boss(http_client=http_client)
                        if status is True:
                            logger.success(f"{self.session_name} | ✅ Successful setting next boss: "
                                        f"<m>{current_boss_level+1}</m>")


                    if active_turbo is False:
                        if (energy_boost_count > 0
                            and available_energy < settings.MIN_AVAILABLE_ENERGY
                            and settings.APPLY_DAILY_ENERGY is True):
                            logger.info(f"{self.session_name} | 😴 Sleep 5s before activating the daily energy boost")
                            await asyncio.sleep(delay=5)

                            status = await self.apply_boost(http_client=http_client, boost_type=FreeBoostType.ENERGY)
                            if status is True:
                                logger.success(f"{self.session_name} | 👉 Energy boost applied")

                                await asyncio.sleep(delay=1)

                            continue

                        if turbo_boost_count > 0 and settings.APPLY_DAILY_TURBO is True:
                            logger.info(f"{self.session_name} | 😴 Sleep 5s before activating the daily turbo boost")
                            await asyncio.sleep(delay=5)

                            status = await self.apply_boost(http_client=http_client, boost_type=FreeBoostType.TURBO)
                            if status is True:
                                logger.success(f"{self.session_name} | 👉 Turbo boost applied")

                                await asyncio.sleep(delay=10)

                                active_turbo = True
                                turbo_time = time()

                            continue

                        if settings.AUTO_UPGRADE_TAP is True and next_tap_level <= settings.MAX_TAP_LEVEL:
                            status = await self.upgrade_boost(http_client=http_client,
                                                            boost_type=UpgradableBoostType.TAP)
                            if status is True:
                                logger.success(f"{self.session_name} | 👉 Tap upgraded to {next_tap_level} lvl")

                                await asyncio.sleep(delay=1)


                        if settings.AUTO_UPGRADE_ENERGY is True and next_energy_level <= settings.MAX_ENERGY_LEVEL:
                            status = await self.upgrade_boost(http_client=http_client,
                                                            boost_type=UpgradableBoostType.ENERGY)
                            if status is True:
                                logger.success(f"{self.session_name} | 👉 Energy upgraded to {next_energy_level} lvl")

                                await asyncio.sleep(delay=1)

                        if settings.AUTO_UPGRADE_CHARGE is True and next_charge_level <= settings.MAX_CHARGE_LEVEL:
                            status = await self.upgrade_boost(http_client=http_client,
                                                            boost_type=UpgradableBoostType.CHARGE)
                            if status is True:
                                logger.success(f"{self.session_name} | 👉 Charge upgraded to {next_charge_level} lvl")

                                await asyncio.sleep(delay=1)
                            
                        if available_energy < settings.MIN_AVAILABLE_ENERGY:
                            logger.info(f"{self.session_name} | 👉 Minimum energy reached: {available_energy}")
                            logger.info(f"{self.session_name} | 😴 Sleep {settings.SLEEP_BY_MIN_ENERGY}s")

                            await asyncio.sleep(delay=settings.SLEEP_BY_MIN_ENERGY)

                            continue

                except InvalidSession as error:
                    raise error

                except Exception as error:
                    logger.error(f"{self.session_name} | ❗️Unknown error with Tapper (unkwnown): {error} | {traceback.format_exc()}")
                    await asyncio.sleep(delay=3)

                else:
                    sleep_between_clicks = randint(a=settings.SLEEP_BETWEEN_TAP[0], b=settings.SLEEP_BETWEEN_TAP[1])

                    if active_turbo is True:
                        sleep_between_clicks = 4
                    elif noBalance is True:
                        sleep_between_clicks = 200

                    logger.info(f"😴 Sleep {sleep_between_clicks}s")
                    await asyncio.sleep(delay=sleep_between_clicks)


async def run_tapper(tg_client: Client, proxy: str | None):
    try:
        await Tapper(tg_client=tg_client).run(proxy=proxy)
    except InvalidSession:
        logger.error(f"{tg_client.name} | ❗️Invalid Session")
