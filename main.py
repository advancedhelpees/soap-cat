import datetime
import discord
import json
import zipfile
from base64 import b64decode
import os
from cleaninty.ctr.simpledevice import SimpleCtrDevice
from cleaninty.ctr.soap.manager import CtrSoapManager
from cleaninty.ctr.soap import helpers
from discord.ext import commands
from dotenv import load_dotenv
from db_abstractor import the_db
from cleaninty_abstractor import cleaninty_abstractor
from cleaninty.nintendowifi.soapenvelopebase import SoapCodeError
from io import BytesIO, StringIO
from pyctr.type.exefs import ExeFSReader


bot = discord.Bot()
load_dotenv()


@bot.slash_command(description="does a soap")
@discord.option(
    "serial",
    str,
    description="the serial on the sticker, use 'skip' to skip the check (only skip if u smart)",
    max_length=11,
)
@discord.option(
    "essential_exefs",
    discord.Attachment,
    required=False,
    description="...the essential.exefs of the console to soap",
)
@discord.option(
    "console_json",
    discord.Attachment,
    required=False,
    description="...the .json of the console to soap",
)
async def doasoap(
    ctx: discord.ApplicationContext,
    serial: str,
    essential_exefs: discord.Attachment,
    console_json: discord.Attachment,
):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    print(f"doing soap for {ctx.author.global_name} ({ctx.author.id})")
    resultStr = str("```")

    if essential_exefs is not None:
        try:
            soap_json = generate_json(await essential_exefs.read())
            soap_name = essential_exefs.filename[:-6]
        except Exception as e:
            await ctx.respond(ephemeral=True, content=f"Failed to load essential\n{e}")
            print("done")
            return
    elif console_json is not None:
        soap_json = await console_json.read()
        soap_name = console_json.filename[:-5]
        if not donorcheck(soap_json):
            ctx.respond(ephemeral=True, content="Failed to verify json")
            print("done")
            return
    else:
        await ctx.respond(
            ephemeral=True,
            content="uh... what? you didn't send a .json or .exefs, try again",
        )
        print("done")
        return

    if serial is not None:
        # .upper() is just for consistency
        soap_serial = get_json_serial(soap_json).upper()
        serial = str(serial).upper()

        if serial == "SKIP":
            resultStr += "skipping serial check\n"

        if len(serial) not in [4, 10, 11]:
            resultStr += f"invalid serial length, must be 10 or 11 characters long instead of {len(serial)}"
            await ctx.respond(ephemeral=True, content=resultStr)
            print("done")
            return

        elif serial[:10] != soap_serial:
            resultStr += f"secinfo serial and given serial do not match!\nsecinfo: {soap_serial}\ngiven: {serial}\n"
            resultStr += "nothing has been done to any donors or the soapee\n```"
            await ctx.respond(ephemeral=True, content=resultStr)
            print("done")
            return
        else:
            resultStr += "secinfo serial and given serial match, continuing\n"

    try:
        dev = SimpleCtrDevice(json_string=soap_json)
        soapMan = CtrSoapManager(dev, False)
        helpers.CtrSoapCheckRegister(soapMan)
        cleaninty = cleaninty_abstractor()
    except Exception as e:
        await ctx.respond(ephemeral=True, content=f"Cleaninty error:\n```\n{e}\n```")
        print("done")
        return

    soap_json = dev.serialize_json()

    if json.loads(soap_json)["region"] == "USA":
        source_region_change = "JPN"
        source_country_change = "JP"
        source_language_change = "ja"
    else:
        source_region_change = "USA"
        source_country_change = "US"
        source_language_change = "en"

    resultStr += "Attempting eShopRegionChange on source...\n"
    try:
        soap_json, resultStr = cleaninty.eshop_region_change(
            json_string=soap_json,
            region=source_region_change,
            country=source_country_change,
            language=source_language_change,
            result_string=resultStr,
        )

    except SoapCodeError as err:
        if err.soaperrorcode != 602:
            print("done")
            raise

        resultStr += "sticky titles are sticking, doing system transfer...\n"

        soap_json, donor_json_name, resultStr = cleaninty.do_transfer_with_donor(
            soap_json, resultStr
        )

        resultStr += f"`{donor_json_name}` is now on cooldown\nDone!"

        helpers.CtrSoapCheckRegister(soapMan)
        soap_json = cleaninty.clean_json(soap_json)

    else:
        resultStr += "sticky titles aren't sticking or don't exist (you won the soap lottery), deleting eShop account...\n"

        soap_json, resultStr = cleaninty.delete_eshop_account(
            json_string=soap_json, result_string=resultStr
        )

    helpers.CtrSoapCheckRegister(soapMan)
    soap_json = cleaninty.clean_json(soap_json)

    print("done")
    resultStr += "Done!\n```"
    await ctx.respond(
        ephemeral=True,
        content=resultStr,
        file=discord.File(fp=StringIO(soap_json), filename=f"{soap_name}.json"),
    )


@bot.slash_command(description="check soap donor availability")
async def soapcheck(ctx: discord.ApplicationContext):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    donors = the_db().read_donor_table()

    embed = discord.Embed(
        title="SOAP check",
        description="Checks what SOAP donors are available",
        color=discord.Color.green(),
    )

    if len(donors) > 9:
        loopcount = 9
    else:
        loopcount = len(donors)

    the_time = int(datetime.datetime.now(datetime.UTC).timestamp())
    for i in range(loopcount):
        donor_transfer_time = donors[i][2] + 604800
        if donor_transfer_time <= the_time:
            embed.add_field(name=donors[i][0], value="Ready!")
        else:
            embed.add_field(
                name=f"{donors[i][0]}", value=f"Ready <t:{donors[i][2] + 604800}:R>"
            )

        if len(donors) > 9:
            embed.set_footer(text="There are more donors that are not shown")

    await ctx.respond(ephemeral=True, embed=embed)


@bot.slash_command(description="uploads a donor to be used for future soaps")
@discord.option("donor_json_file", discord.Attachment, required=False)
@discord.option("donor_exefs_file", discord.Attachment, required=False)
@discord.option(
    "note",
    str,
    required=False,
    description="any notes you want attached to the donor",
)
async def uploaddonortodb(
    ctx: discord.ApplicationContext,
    donor_json_file: discord.Attachment,
    donor_exefs_file: discord.Attachment,
    note: str,
):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    if donor_exefs_file is not None:
        if not donor_exefs_file.filename[-6:] == ".exefs":
            await ctx.respond(ephemeral=True, content="not a .exefs!")
            return
        try:
            donor_json = generate_json(essential=await donor_exefs_file.read())
            donor_name = donor_exefs_file.filename[:-6]
        except Exception as e:
            await ctx.respond(ephemeral=True, content=e)
            return

    elif donor_json_file is not None:
        if not donor_json_file.filename[-5:] == ".json":
            await ctx.respond(ephemeral=True, content="not a .json!")
            return
        try:
            donor_json = await donor_json_file.read()
            donor_json = donor_json.decode("utf-8")
            json.loads(donor_json)  # Validate the json, output useless
            donor_name = donor_json_file.filename[:-5]
        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to load json")
            return

    else:
        await ctx.respond(
            ephemeral=True,
            content="uh... what? you didn't send a .json or .exefs, try again",
        )
        return

    if not donorcheck(donor_json):
        await ctx.respond(
            ephemeral=True,
            content="not a valid donor!\nif you believe this to be a mistake contact crudefern",
        )
        return

    if len(note) > 128:
        await ctx.respond(
            ephemeral=True, content="note too long! max is 128 characters"
        )
        return

    mySQL_DB = the_db()
    cleaninty = cleaninty_abstractor()

    mySQL_DB.cursor.execute("SELECT * FROM donors WHERE name = %s", (donor_name,))

    if len(mySQL_DB.cursor.fetchall()) != 0:
        await ctx.respond(
            ephemeral=True, content=f"`{donor_name}` is already in the db!"
        )
        return

    if json.loads(donor_json)["region"] == "USA":
        donor_region_change = "JPN"
        donor_country_change = "JP"
        donor_language_change = "ja"
    else:
        donor_region_change = "USA"
        donor_country_change = "US"
        donor_language_change = "en"

    print(f"uploading donor from {ctx.author.global_name} ({ctx.author.id})")

    try:
        donor_json = cleaninty.eshop_region_change(
            json_string=donor_json,
            region=donor_region_change,
            country=donor_country_change,
            language=donor_language_change,
            result_string="",
        )[0]
    except SoapCodeError as err:
        if err.soaperrorcode != 602:
            raise

        donor_json = cleaninty.do_transfer_with_donor(donor_json, "")[0]

        donor_json = cleaninty.eshop_region_change(
            json_string=donor_json,
            region=donor_region_change,
            country=donor_country_change,
            language=donor_language_change,
            result_string="",
        )[0]

    mySQL_DB.write_donor(
        name=donor_name,
        json=cleaninty.clean_json(donor_json),
        last_transferred=cleaninty.get_last_moved_time(donor_json),
        uploader=ctx.author.id,
        note=note,
    )

    await ctx.respond(
        ephemeral=True,
        content=f"`{donor_name}` has been uploaded to the donor database\nwant to remove it? contact crudefern",
    )
    print(f"{ctx.author.global_name} ({ctx.author.id}) uploaded {donor_name} to the db")


@bot.slash_command(description="get the info of a donor")
@discord.option("name", str)
async def donorinfo(ctx: discord.ApplicationContext, name: str):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    embed = discord.Embed(color=discord.Color.green(), title=f"# info about `{name}`")

    donor = the_db().read_index(table="donors", index_field_name="name", index=name)
    uploader = await ctx.bot.fetch_user(donor[3])
    embed.set_thumbnail(url=uploader.display_avatar.url)

    if donor is None:
        await ctx.respond(ephemeral=True, content=f"`{name}` not found")
        return

    embed.add_field(name="Uploader:", value=uploader.name)
    embed.add_field(name="Last transfer time:", value=f"<t:{donor[2]}:f>")
    embed.add_field(name="Note:", value=donor[4])

    await ctx.respond(ephemeral=True, embed=embed)


@bot.slash_command(description="downloads all donors")
@commands.is_owner()
async def downloaddonors(ctx: discord.ApplicationContext):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    donors = the_db().read_donor_table()
    output = BytesIO()
    output.write(
        b"\x50\x4b\x05\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )  # zip file header
    output.seek(0)
    zip = zipfile.ZipFile(output, "w")

    for i in range(len(donors)):
        donor_file = bytes(donors[i][1].encode("ascii"))

        donor_name = f"{donors[i][0]}.json"
        zip.writestr(donor_name, donor_file)
    zip.close()

    await ctx.respond(
        ephemeral=True,
        file=discord.File(fp=BytesIO(output.getvalue()), filename="donors.zip"),
    )


def donorcheck(input_json: str) -> bool:
    try:
        input_json_obj = json.loads(input_json)
    except json.decoder.JSONDecodeError:
        return False

    if len(input_json_obj["otp"]) != 344:
        return False
    if len(input_json_obj["msed"]) != 428:
        return False
    if len(input_json_obj["region"]) != 3:
        return False
    return True


def generate_json(  # thanks soupman
    essential,
) -> str:
    try:
        reader = ExeFSReader(BytesIO(essential))
    except Exception:
        raise Exception("Failed to read essential")

    if not "secinfo" and "otp" in reader.entries:
        raise Exception("Invalid essential")

    secinfo = reader.open("secinfo")
    secinfo.seek(0x100)
    country_byte = secinfo.read(1)

    if country_byte == b"\x01":
        country = "US"
    elif country_byte == b"\x02":
        country = "GB"
    else:
        country = None

    try:
        generated_json = SimpleCtrDevice.generate_new_json(
            otp_data=reader.open("otp").read(),
            secureinfo_data=reader.open("secinfo").read(),
            country=country,
        )
    except Exception as e:
        raise Exception(f"Cleaninty error:\n```\n{e}\n```")

    return generated_json


def get_json_serial(json_string: str) -> str:
    json_secinfo = b64decode(str(json.loads(json_string)["secureinfo"]).encode("ascii"))
    serial_bytes = bytes(json_secinfo[0x102:0x112]).replace(b"\x00", b"")
    return serial_bytes.upper().decode("utf-8")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} successfully!")
    print(
        discord.utils.oauth_url(
            bot.user.id, permissions=discord.Permissions(permissions=2147518464)
        )
    )
    await bot.change_presence(activity=discord.Game(name="I HAS SOAP *om nom nom*"))


@bot.event
async def on_application_command_error(
    ctx: discord.ApplicationContext, error: discord.DiscordException
):
    if isinstance(error, commands.NotOwner):
        await ctx.respond(ephemeral=True, content="you can't use this command!")
    else:
        await ctx.respond(
            ephemeral=True,
            content="an error has occurred, please do not try again",
        )
        raise


bot.load_extension("soupman")

bot.run(os.getenv("DISCORD_TOKEN"))
