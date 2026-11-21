"""Zitate-Sammlung: Rechtsklick auf eine Nachricht → Apps → „Als Zitat speichern“, /zitat holt eins raus."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands

from .actions import TZ
from .serverdata import ServerData

QUOTE_COLORS = [0xFEE75C, 0xEB459E, 0x5865F2, 0x57F287, 0xED4245]


def quote_embed(q: dict, guild: discord.Guild) -> discord.Embed:
    member = guild.get_member(q["author_id"])
    name = member.display_name if member else q["author_name"]
    e = discord.Embed(description=f"„{q['text']}“", color=QUOTE_COLORS[q["id"] % len(QUOTE_COLORS)])
    e.set_author(name=name, icon_url=member.display_avatar.url if member else None)
    e.add_field(name="​", value=f"[Zur Nachricht]({q['link']}) · "
                                     f"{datetime.fromtimestamp(q['said_at'], TZ):%d.%m.%Y}", inline=False)
    e.set_footer(text=f"Zitat #{q['id']}")
    return e


def register_quotes(tree: app_commands.CommandTree, data: ServerData) -> None:
    @tree.context_menu(name="Als Zitat speichern")
    @app_commands.guild_only()
    async def save_quote(interaction: discord.Interaction, message: discord.Message):
        text = message.clean_content.strip()
        if not text:
            await interaction.response.send_message("Da ist kein Text drin, den ich speichern könnte.", ephemeral=True)
            return
        if len(text) > 1000:
            await interaction.response.send_message("Das ist zu lang für ein Zitat (max. 1000 Zeichen).",
                                                    ephemeral=True)
            return
        qid = data.add_quote(interaction.guild_id, message.author.id, message.author.display_name, text,
                             message.jump_url, interaction.user.id, message.created_at.timestamp())
        if qid is None:
            await interaction.response.send_message("Das ist schon gespeichert. 😄", ephemeral=True)
            return
        await interaction.response.send_message(
            f"💬 **{interaction.user.display_name}** hat ein Zitat von **{message.author.display_name}** gespeichert! "
            f"(#{qid} · `/zitat` holt zufällige raus)", allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="zitat", description="Ein zufälliges gespeichertes Zitat")
    @app_commands.describe(von="Nur Zitate von dieser Person")
    @app_commands.guild_only()
    async def zitat(interaction: discord.Interaction, von: discord.Member | None = None):
        q = data.random_quote(interaction.guild_id, von.id if von else None)
        if q is None:
            hint = f"von {von.display_name} " if von else ""
            await interaction.response.send_message(
                f"Noch keine Zitate {hint}gespeichert. Rechtsklick auf eine Nachricht → Apps → „Als Zitat speichern“.",
                ephemeral=True)
            return
        await interaction.response.send_message(embed=quote_embed(q, interaction.guild))

    @tree.command(name="zitat_loeschen", description="Löscht ein Zitat (eigene, selbst gespeicherte oder als Admin)")
    @app_commands.describe(nummer="Die Nummer unten am Zitat (#…)")
    @app_commands.guild_only()
    async def zitat_loeschen(interaction: discord.Interaction, nummer: int):
        admin = interaction.user.guild_permissions.manage_messages
        ok = data.delete_quote(interaction.guild_id, nummer, interaction.user.id, admin)
        await interaction.response.send_message("Zitat gelöscht." if ok else
                                                "Das Zitat gibt's nicht oder du darfst es nicht löschen.",
                                                ephemeral=True)
