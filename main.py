import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════
#  KONFIGURÁCIA
# ══════════════════════════════════════════

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

MIN_STEAM_DISCOUNT = 75
CHECK_INTERVAL_HOURS = 6
SEEN_GAMES_FILE = "seen_games.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/javascript, */*",
    "Referer": "https://store.steampowered.com/",
}

# ══════════════════════════════════════════
#  INICIALIZÁCIA BOTA
# ══════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def load_seen_games():
    if os.path.exists(SEEN_GAMES_FILE):
        with open(SEEN_GAMES_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_games(seen: set):
    with open(SEEN_GAMES_FILE, "w") as f:
        json.dump(list(seen), f)

seen_games = load_seen_games()

# ══════════════════════════════════════════
#  EPIC GAMES
# ══════════════════════════════════════════

async def get_epic_free_games():
    url = (
        "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        "?locale=sk&country=SK&allowCountries=SK"
    )
    games = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    print(f"[Epic] HTTP {resp.status}")
                    return games
                data = await resp.json()

        elements = (
            data.get("data", {})
                .get("Catalog", {})
                .get("searchStore", {})
                .get("elements", [])
        )

        for game in elements:
            promotions = game.get("promotions") or {}

            for promo in promotions.get("promotionalOffers", []):
                for offer in promo.get("promotionalOffers", []):
                    if offer.get("discountSetting", {}).get("discountPercentage", 100) == 0:
                        title = game.get("title", "Neznáma hra")
                        slug = game.get("productSlug") or game.get("urlSlug") or ""
                        url_game = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games"
                        img = next((ki.get("url", "") for ki in game.get("keyImages", [])
                                    if ki.get("type") in ("Thumbnail", "DieselStoreFrontWide", "OfferImageWide")), "")
                        games.append({
                            "title": title, "url": url_game, "image": img,
                            "end_date": offer.get("endDate", ""),
                            "type": "epic_free",
                        })

            for promo in promotions.get("upcomingPromotionalOffers", []):
                for offer in promo.get("promotionalOffers", []):
                    if offer.get("discountSetting", {}).get("discountPercentage", 100) == 0:
                        title = game.get("title", "Neznáma hra")
                        slug = game.get("productSlug") or game.get("urlSlug") or ""
                        url_game = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games"
                        img = next((ki.get("url", "") for ki in game.get("keyImages", [])
                                    if ki.get("type") in ("Thumbnail", "DieselStoreFrontWide", "OfferImageWide")), "")
                        games.append({
                            "title": title, "url": url_game, "image": img,
                            "start_date": offer.get("startDate", ""),
                            "type": "epic_upcoming",
                        })

        print(f"[Epic] Nájdených: {len(games)}")
    except Exception as e:
        print(f"[Epic] Chyba: {e}")
    return games

# ══════════════════════════════════════════
#  STEAM
# ══════════════════════════════════════════

async def get_steam_deals():
    deals = []
    seen_appids = set()

    # ── Endpoint 1: Featured Categories ──────────────────────────
    try:
        url = "https://store.steampowered.com/api/featuredcategories/?cc=sk&l=english"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[Steam Featured] HTTP: {resp.status}")
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for key in ["specials", "top_sellers", "new_releases", "coming_soon"]:
                        items = data.get(key, {}).get("items", [])
                        print(f"[Steam Featured] '{key}': {len(items)} položiek")
                        for item in items:
                            appid = str(item.get("id", ""))
                            if not appid or appid in seen_appids:
                                continue
                            name = item.get("name", "")
                            if not name:
                                continue
                            discount = item.get("discount_percent") or 0
                            final_price = item.get("final_price") or 0
                            original_price = item.get("original_price") or 0
                            original_eur = original_price / 100
                            final_eur = final_price / 100
                            img = (item.get("large_capsule_image") or item.get("small_capsule_image") or
                                   f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg")
                            url_game = f"https://store.steampowered.com/app/{appid}/"

                            if discount == 100 or (final_price == 0 and original_price > 0):
                                seen_appids.add(appid)
                                deals.append({
                                    "title": name, "url": url_game, "image": img,
                                    "discount": 100, "original_price": original_eur, "final_price": 0,
                                    "type": "steam_free", "appid": appid,
                                })
                            elif discount >= MIN_STEAM_DISCOUNT:
                                seen_appids.add(appid)
                                deals.append({
                                    "title": name, "url": url_game, "image": img,
                                    "discount": discount, "original_price": original_eur, "final_price": final_eur,
                                    "type": "steam_deal", "appid": appid,
                                })
    except Exception as e:
        print(f"[Steam Featured] Chyba: {e}")

    # ── Endpoint 2: Steam Featured hlavná stránka ─────────────────
    try:
        url = "https://store.steampowered.com/api/featured/?cc=sk&l=english"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[Steam Main] HTTP: {resp.status}")
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for category in ["large_capsules", "featured_win", "featured_mac", "featured_linux"]:
                        for item in data.get(category, []):
                            appid = str(item.get("id", ""))
                            if not appid or appid in seen_appids:
                                continue
                            name = item.get("name", "")
                            if not name:
                                continue
                            discount = item.get("discount_percent") or 0
                            final_price = item.get("final_price") or 0
                            original_price = item.get("original_price") or 0
                            original_eur = original_price / 100
                            final_eur = final_price / 100
                            img = (item.get("large_capsule_image") or
                                   f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg")
                            url_game = f"https://store.steampowered.com/app/{appid}/"

                            if discount == 100 or (final_price == 0 and original_price > 0):
                                seen_appids.add(appid)
                                deals.append({
                                    "title": name, "url": url_game, "image": img,
                                    "discount": 100, "original_price": original_eur, "final_price": 0,
                                    "type": "steam_free", "appid": appid,
                                })
                            elif discount >= MIN_STEAM_DISCOUNT:
                                seen_appids.add(appid)
                                deals.append({
                                    "title": name, "url": url_game, "image": img,
                                    "discount": discount, "original_price": original_eur, "final_price": final_eur,
                                    "type": "steam_deal", "appid": appid,
                                })
    except Exception as e:
        print(f"[Steam Main] Chyba: {e}")

    # ── Endpoint 3: Steam Search free hry ────────────────────────
    try:
        url = "https://store.steampowered.com/search/results/?specials=1&maxprice=free&json=1&count=50&cc=sk"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[Steam Free Search] HTTP: {resp.status}")
                if resp.status == 200:
                    text = await resp.text()
                    if text.strip().startswith("{") or text.strip().startswith("["):
                        data = json.loads(text)
                        items = data.get("items", [])
                        print(f"[Steam Free Search] Nájdených: {len(items)}")
                        for item in items:
                            print(f"[Steam Free Search] Hra: {item.get('name')} | appid: {item.get('id')}")
                            appid = str(item.get("id", ""))
                            if not appid or appid in seen_appids:
                                continue
                            name = item.get("name", "")
                            if not name:
                                continue
                            seen_appids.add(appid)
                            img = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
                            url_game = f"https://store.steampowered.com/app/{appid}/"
                            deals.append({
                                "title": name, "url": url_game, "image": img,
                                "discount": 100, "original_price": 0, "final_price": 0,
                                "type": "steam_free", "appid": appid,
                            })
                    else:
                        print(f"[Steam Free Search] Dostal HTML namiesto JSON (Steam blokuje)")
    except Exception as e:
        print(f"[Steam Free Search] Chyba: {e}")

    # ── Endpoint 4: SteamDB RSS feed (TEST) ──────────────────────
    try:
        rss_url = "https://steamdb.info/sales/feed/"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(rss_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[SteamDB RSS] HTTP: {resp.status}")
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    rss_items = root.findall(".//item")
                    print(f"[SteamDB RSS] Nájdených: {len(rss_items)}")
                    for rss_item in rss_items[:10]:
                        print(f"[SteamDB RSS] {rss_item.findtext('title')}")
    except Exception as e:
        print(f"[SteamDB RSS] Chyba: {e}")

    print(f"[Steam] Celkom nájdených: {len(deals)}")
    return deals

# ══════════════════════════════════════════
#  EMBED TVORBA
# ══════════════════════════════════════════

def make_epic_embed(game: dict) -> discord.Embed:
    is_upcoming = game["type"] == "epic_upcoming"
    color = 0x2ECC71 if not is_upcoming else 0xF39C12

    if is_upcoming:
        title = f"🔜 Čoskoro zadarmo: {game['title']}"
        desc = "Táto hra bude čoskoro dostupná **zadarmo** na Epic Games Store!"
        start = game.get("start_date", "")
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                desc += f"\n📅 Dostupné od: **{dt.strftime('%d.%m.%Y %H:%M')} UTC**"
            except Exception:
                pass
    else:
        title = f"🎮 ZADARMO na Epic Games: {game['title']}"
        desc = f"**{game['title']}** je teraz dostupná **úplne zadarmo** na Epic Games Store!"
        end = game.get("end_date", "")
        if end:
            try:
                dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                desc += f"\n⏰ Ponuka končí: **{dt.strftime('%d.%m.%Y %H:%M')} UTC**"
            except Exception:
                pass

    embed = discord.Embed(title=title, description=desc, url=game["url"], color=color)
    embed.set_author(name="Epic Games Store", icon_url="https://i.imgur.com/8UMiO2k.png")
    if game.get("image"):
        embed.set_image(url=game["image"])
    embed.add_field(name="🔗 Získaj zadarmo", value=f"[Klikni sem]({game['url']})", inline=False)
    embed.set_footer(text=f"GameDealsBot • {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    return embed


def make_steam_embed(deal: dict) -> discord.Embed:
    if deal["type"] == "steam_free":
        color = 0x2ECC71
        title = f"🎮 ZADARMO na Steame: {deal['title']}"
        desc = f"**{deal['title']}** je teraz dostupná **úplne zadarmo** na Steame!"
    elif deal["discount"] >= 90:
        color = 0xE74C3C
        title = f"🔥 MEGA ZĽAVA na Steame: {deal['title']}"
        desc = (
            f"**{deal['title']}** má obrovskú zľavu!\n\n"
            f"~~{deal['original_price']:.2f} €~~ → **{deal['final_price']:.2f} €**  "
            f"(**-{deal['discount']}%**)"
        )
    else:
        color = 0x3498DB
        title = f"💸 Veľká zľava na Steame: {deal['title']}"
        desc = (
            f"**{deal['title']}** má veľkú zľavu!\n\n"
            f"~~{deal['original_price']:.2f} €~~ → **{deal['final_price']:.2f} €**  "
            f"(**-{deal['discount']}%**)"
        )

    embed = discord.Embed(title=title, description=desc, url=deal["url"], color=color)
    embed.set_author(name="Steam Store", icon_url="https://i.imgur.com/xxr2UMQ.png")
    if deal.get("image"):
        embed.set_image(url=deal["image"])
    embed.add_field(name="🛒 Získaj teraz", value=f"[Otvoriť na Steame]({deal['url']})", inline=False)
    embed.set_footer(text=f"GameDealsBot • {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    return embed

# ══════════════════════════════════════════
#  HLAVNÁ KONTROLA
# ══════════════════════════════════════════

async def check_and_post():
    global seen_games
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[Bot] Kanál s ID {CHANNEL_ID} nebol nájdený!")
        return

    new_count = 0

    epic_games = await get_epic_free_games()
    for game in epic_games:
        uid = f"epic_{game['type']}_{game['title']}"
        if uid in seen_games:
            continue
        embed = make_epic_embed(game)
        await channel.send(embed=embed)
        seen_games.add(uid)
        new_count += 1
        await asyncio.sleep(1)

    steam_deals = await get_steam_deals()
    for deal in steam_deals:
        uid = f"steam_{deal['appid']}_{deal['discount']}"
        if uid in seen_games:
            continue
        embed = make_steam_embed(deal)
        await channel.send(embed=embed)
        seen_games.add(uid)
        new_count += 1
        await asyncio.sleep(1)

    save_seen_games(seen_games)
    print(f"[Bot] Kontrola dokončená. Nových oznámení: {new_count}")

# ══════════════════════════════════════════
#  ÚLOHA
# ══════════════════════════════════════════

@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def periodic_check():
    print(f"[Bot] Spúšťam pravidelú kontrolu ({datetime.now().strftime('%d.%m.%Y %H:%M')})...")
    await check_and_post()

# ══════════════════════════════════════════
#  PRÍKAZY
# ══════════════════════════════════════════

@bot.command(name="check")
async def manual_check(ctx):
    await ctx.send("🔍 Kontrolujem hry a zľavy...")
    await check_and_post()
    await ctx.send("✅ Hotovo!")

@bot.command(name="clearhistory")
async def clear_history(ctx):
    global seen_games
    seen_games = set()
    save_seen_games(seen_games)
    await ctx.send("🗑️ História vymazaná. Ďalšia kontrola ukáže všetko znova.")

@bot.command(name="status")
async def status(ctx):
    embed = discord.Embed(title="🤖 GameDeals Bot – Status", color=0x9B59B6)
    embed.add_field(name="Kontrola každých", value=f"{CHECK_INTERVAL_HOURS} hodín", inline=True)
    embed.add_field(name="Min. Steam zľava", value=f"{MIN_STEAM_DISCOUNT}%", inline=True)
    embed.add_field(name="Zaznamenaných hier", value=str(len(seen_games)), inline=True)
    embed.add_field(name="Kanál", value=f"<#{CHANNEL_ID}>", inline=False)
    embed.set_footer(text=f"Čas: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    await ctx.send(embed=embed)

# ══════════════════════════════════════════
#  SPUSTENIE
# ══════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Bot prihlásený ako: {bot.user} (ID: {bot.user.id})")
    print(f"📢 Cieľový kanál ID: {CHANNEL_ID}")
    print(f"⏱  Interval kontroly: každých {CHECK_INTERVAL_HOURS} hodín")
    print(f"💸 Min. Steam zľava: {MIN_STEAM_DISCOUNT}%")
    periodic_check.start()
    await asyncio.sleep(3)
    await check_and_post()


bot.run(DISCORD_BOT_TOKEN)
