import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════
#  KONFIGURÁCIA
# ══════════════════════════════════════════

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# Minimálna zľava na Steame (v %), ktorú bot nahlási
MIN_STEAM_DISCOUNT = 75

# Ako často kontrolovať (v hodinách)
CHECK_INTERVAL_HOURS = 6

# Súbor na ukladanie již oznámených hier (aby sa neopakovali)
SEEN_GAMES_FILE = "seen_games.json"

# User-Agent aby Steam neblokoval requesty
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
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
#  EPIC GAMES – Free Games (oficiálne API)
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
            offers = promotions.get("promotionalOffers", [])
            upcoming = promotions.get("upcomingPromotionalOffers", [])

            # Aktuálne free hry
            for promo in offers:
                for offer in promo.get("promotionalOffers", []):
                    discount = offer.get("discountSetting", {}).get("discountPercentage", 100)
                    if discount == 0:
                        title = game.get("title", "Neznáma hra")
                        slug = game.get("productSlug") or game.get("urlSlug") or ""
                        url_game = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games"
                        img = ""
                        for ki in game.get("keyImages", []):
                            if ki.get("type") in ("Thumbnail", "DieselStoreFrontWide", "OfferImageWide"):
                                img = ki.get("url", "")
                                break
                        end_date = offer.get("endDate", "")
                        games.append({
                            "title": title,
                            "url": url_game,
                            "image": img,
                            "end_date": end_date,
                            "type": "epic_free",
                            "source": "Epic Games",
                        })

            # Budúce free hry (preview)
            for promo in upcoming:
                for offer in promo.get("promotionalOffers", []):
                    discount = offer.get("discountSetting", {}).get("discountPercentage", 100)
                    if discount == 0:
                        title = game.get("title", "Neznáma hra")
                        slug = game.get("productSlug") or game.get("urlSlug") or ""
                        url_game = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games"
                        img = ""
                        for ki in game.get("keyImages", []):
                            if ki.get("type") in ("Thumbnail", "DieselStoreFrontWide", "OfferImageWide"):
                                img = ki.get("url", "")
                                break
                        start_date = offer.get("startDate", "")
                        games.append({
                            "title": title,
                            "url": url_game,
                            "image": img,
                            "start_date": start_date,
                            "type": "epic_upcoming",
                            "source": "Epic Games (čoskoro)",
                        })
    except Exception as e:
        print(f"[Epic] Chyba: {e}")
    return games


# ══════════════════════════════════════════
#  STEAM – Free hry + Veľké zľavy
# ══════════════════════════════════════════

async def get_steam_deals():
    deals = []

    # --- Free hry cez Steam Store API ---
    free_url = "https://store.steampowered.com/api/featuredcategories/?cc=sk&l=english"
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(free_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[Steam Featured] Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    specials = data.get("specials", {}).get("items", [])
                    print(f"[Steam Featured] Počet položiek: {len(specials)}")
                    for item in specials:
                        discount = item.get("discount_percent", 0)
                        appid = str(item.get("id", ""))
                        name = item.get("name", "")
                        if not appid or not name:
                            continue
                        original = item.get("original_price", 0) / 100
                        final = item.get("final_price", 0) / 100
                        img = item.get("large_capsule_image") or f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
                        url_game = f"https://store.steampowered.com/app/{appid}/"

                        if discount == 100 or final == 0:
                            deals.append({
                                "title": name, "url": url_game, "image": img,
                                "discount": 100, "original_price": original, "final_price": 0,
                                "type": "steam_free", "source": "Steam", "appid": appid,
                            })
                        elif discount >= MIN_STEAM_DISCOUNT:
                            deals.append({
                                "title": name, "url": url_game, "image": img,
                                "discount": discount, "original_price": original, "final_price": final,
                                "type": "steam_deal", "source": "Steam", "appid": appid,
                            })
    except Exception as e:
        print(f"[Steam Featured] Chyba: {e}")

    # --- Záloha: IsThereAnyDeal API (nevyžaduje kľúč pre základné dáta) ---
    itad_url = "https://api.isthereanydeal.com/v01/deals/list/?key=&offset=0&limit=20&region=sk&country=SK"
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(itad_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[ITAD] Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    list_data = data.get("data", {}).get("list", [])
                    print(f"[ITAD] Počet položiek: {len(list_data)}")
                    for item in list_data:
                        price_new = item.get("price_new", 999)
                        price_old = item.get("price_old", 0)
                        if price_old == 0:
                            continue
                        discount = round((1 - price_new / price_old) * 100)
                        if price_new == 0:
                            game_type = "steam_free"
                        elif discount >= MIN_STEAM_DISCOUNT:
                            game_type = "steam_deal"
                        else:
                            continue
                        title = item.get("title", "")
                        appid = str(item.get("plain", ""))
                        url_game = item.get("url", "https://store.steampowered.com")
                        deals.append({
                            "title": title, "url": url_game, "image": "",
                            "discount": discount, "original_price": price_old, "final_price": price_new,
                            "type": game_type, "source": "Steam", "appid": f"itad_{appid}",
                        })
    except Exception as e:
        print(f"[ITAD] Chyba: {e}")

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
        desc = f"Táto hra bude čoskoro dostupná **zadarmo** na Epic Games Store!"
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

    # --- Epic Games ---
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

    # --- Steam ---
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
#  ÚLOHA (opakuje sa každých N hodín)
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
    """Manuálna kontrola hier (!check)"""
    await ctx.send("🔍 Kontrolujem hry a zľavy...")
    await check_and_post()
    await ctx.send("✅ Hotovo!")

@bot.command(name="clearhistory")
async def clear_history(ctx):
    """Vymazanie histórie videných hier (!clearhistory)"""
    global seen_games
    seen_games = set()
    save_seen_games(seen_games)
    await ctx.send("🗑️ História videných hier bola vymazaná. Ďalšia kontrola ukáže všetko znova.")

@bot.command(name="status")
async def status(ctx):
    """Informácie o bote (!status)"""
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
