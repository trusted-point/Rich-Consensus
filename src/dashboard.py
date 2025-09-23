import re
import asyncio

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from src.calls import AioHttpCalls
from src.converter import pubkey_to_hex
from utils.logger import logger
from rich import box
from datetime import datetime, timezone, timedelta
from typing import Callable
from aiohttp import ClientSession

class ConsensusDashboard:
    def __init__(
        self,
        rpc: str,
        refresh_per_second: int,
        disable_emojis: bool,
        refresh_validators: int,
        refresh_consensus_state: int,
        refresh_upgrade_plan: int,
        refresh_node: int,
        refresh_block_time: int,
        user_tz: timedelta,
        block_time_number: int,
        columns: int,
        hashes: bool,
        session: ClientSession,
        genesis: str
    ):
        
        self.rpc = rpc
        self.session = session
        self.genesis = genesis
        self.rich_logger = None
        for handler in logger.handlers:
            if "RichPanelLogHandler" in str(handler):
                self.rich_logger = handler
                break

        self.layout = Layout()
        self.console = Console()
        self.validators = []

        self.consensus_state = {}

        self.block_time = None
        self.node_height = None
        self.chain_id = None
        self.synced = False

        self.upgrade_name = None
        self.upgrade_height = None
        self.upgrade_time_left_seconds = None

        self.online_validators = 0

        self.refresh_validators = refresh_validators
        self.refresh_consensus_state = refresh_consensus_state
        self.refresh_upgrade_plan = refresh_upgrade_plan
        self.refresh_per_second = refresh_per_second
        self.refresh_node = refresh_node
        self.refresh_block_time = refresh_block_time
        self.disable_emojis = disable_emojis
        self.hashes = hashes
        self.block_time_check_number = block_time_number
        self.columns = columns
        self.user_tz = user_tz

        self.previous_node_status = {}
        self.previous_consensus_state = {}
        self.previous_validators = []
        self.previous_upgrade_info = {}
        self.previous_block_time = None

        self.layout.split_column(
            Layout(name="header", ratio=2),
            Layout(name="main", ratio=8),
            Layout(name="footer", ratio=1),
        )

        self.layout["header"].split_row(
            Layout(name="network_info", ratio=1),
            Layout(name="consensus_info", ratio=1),
            Layout(name="logs", ratio=1),
        )

    async def update_node_status(self):
        try:
            async with AioHttpCalls(rpc=self.rpc, session=self.session) as session:
                rpc_status = await session.get_rpc_status()
            if rpc_status:
                self.synced = (
                    True
                    if rpc_status.get("sync_info", {}).get("catching_up") is False
                    else False
                )
                self.chain_id = rpc_status.get("node_info", {}).get("network", "N/A")
                self.node_height = (
                    int(rpc_status["sync_info"]["latest_block_height"])
                    if rpc_status.get("sync_info", {}).get("latest_block_height")
                    else None
                )
                logger.info(f"Updated node status | Height: {self.node_height}")
            else:
                logger.error(f"Failed to update node status")
        except Exception as e:
            logger.error(f"An error occurred while updating node status {e}")

    async def update_upgrade(self):
        try:
            async with AioHttpCalls(rpc=self.rpc, session=self.session) as session:
                upgrade_plan = await session.get_upgrade_info()
            if upgrade_plan:
                upgrade_plan = upgrade_plan.get("plan", {})
                if "height" in upgrade_plan:
                    self.upgrade_height = int(upgrade_plan["height"])
                    self.upgrade_name = upgrade_plan.get("name", "N/A")
                logger.info(f"Updated upgrade plan [{self.upgrade_name} | {self.upgrade_height}]")
            else:
                logger.info("No upgrade detected")
                self.upgrade_height = None
                self.upgrade_name = None
        except Exception as e:
            logger.error(f"An error occurred while updating upgrade plan: {e}")

    def demojize(self, moniker):
        try:
            emoji_pattern = re.compile("[\U00010000-\U0010ffff]", flags=re.UNICODE)
            allowed_pattern = re.compile(r"[^a-zA-Z0-9_\-& ]")
            text_without_emojis = emoji_pattern.sub("", moniker)
            cleaned_text = allowed_pattern.sub("", text_without_emojis)
            return cleaned_text.strip()
        except Exception as e:
            logger.warning(
                f"An error occurred while demojizing validator's moniker {moniker}: {e}"
            )
            return moniker

    async def update_validators(self):
        try:
            async with AioHttpCalls(rpc=self.rpc, session=self.session) as session:
                data = await session.get_validators(status="BOND_STATUS_BONDED")
                if not data:
                    logger.error("Failed to fetch validators. Will retry")
                    return

                if data:
                    sorted_vals = sorted(
                        data, key=lambda x: int(x["tokens"]), reverse=True
                    )
                    validators = []
                    total_stake = sum(int(x["tokens"]) for x in sorted_vals)
                    for validator in sorted_vals:
                        consensus_pubkey_info = validator.get("consensus_pubkey", {})
                        consensus_pub_key = consensus_pubkey_info.get("key")
                        consensus_pub_key_type = consensus_pubkey_info.get("@type")

                        if not consensus_pub_key or not consensus_pub_key_type:
                            missing_fields = []
                            if not consensus_pub_key:
                                missing_fields.append("consensus_pub_key")
                            if not consensus_pub_key_type:
                                missing_fields.append("consensus_pub_key_type")
                            
                            logger.warning(
                                f"Skipping validator due to missing fields ({', '.join(missing_fields)}): {validator}"
                            )
                            continue
                        
                        try:
                            _hex = pubkey_to_hex(pub_key=consensus_pub_key, key_type=consensus_pub_key_type)
                        except (Exception, ValueError) as e:
                            logger.error(f"Failed to convert validator's ({consensus_pubkey_info}) pub key to hex: {e}")
                            continue

                        _moniker = self.demojize(
                            validator.get("description", {}).get("moniker", "N/A")
                        )
                        _tokens = int(validator["tokens"])

                        validators.append(
                            {
                                "moniker": _moniker,
                                "hex": _hex,
                                "vp": round((_tokens / total_stake) * 100, 4)
                            }
                        )

                    self.validators = validators
                    logger.info(
                        f"Updated validators | Current active set: {len(self.validators)}"
                    )

        except Exception as e:
            logger.error(f"An error occurred while updating validators {e}")

    async def set_genesis_validators(self):
        try:
            if not self.validators:
                async with AioHttpCalls(rpc=self.rpc, session=self.session, timeout=30) as session:
                    genesis_data = await session.get_genesis(genesis_url=self.genesis)

                    if not genesis_data:
                        logger.error("Failed to get genesis. Will retry")
                        return

                    if genesis_data:
                        total_tokens = 0
                        validators = []
                        for tx in genesis_data['app_state']['genutil']['gen_txs']:
                            for msg in tx['body']['messages']:
                                if '@type' in msg and msg['@type'] == '/cosmos.staking.v1beta1.MsgCreateValidator':
                                    consensus_pubkey_info = msg['pubkey']
                                    total_tokens += int(msg['value']['amount'])

                                    try:
                                        _hex = pubkey_to_hex(pub_key=consensus_pubkey_info['key'], key_type=consensus_pubkey_info['@type'])
                                    except (Exception, ValueError) as e:
                                        logger.error(f"Failed to convert validator's ({consensus_pubkey_info}) pub key to hex: {e}")
                                        continue

                                    validator = {
                                        'moniker': self.demojize(msg['description']['moniker']),
                                        'tokens': int(msg['value']['amount']),
                                        'hex': _hex
                                    }
                                    validators.append(validator)
                        sorted_vals = sorted(validators, key=lambda x: x['tokens'], reverse=True)

                        for val in sorted_vals:
                            val['vp'] = round((val['tokens'] / total_tokens) * 100, 4)

                        self.validators = sorted_vals
                        logger.info(
                            f"Updated genesis validators | Current active set: {len(self.validators)}"
                        )

        except Exception as e:
            logger.error(f"An error occurred while updating validators {e}")

    def block_time_to_unix(self, time: str) -> float:
        time_str = time[:-4] + time[-1]
        timestamp = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        timestamp_utc = timestamp.replace(tzinfo=timezone.utc)
        return timestamp_utc.timestamp()

    async def update_block_time(self):
        try:
            async with AioHttpCalls(rpc=self.rpc, session=self.session) as session:
                node_status = await session.get_rpc_status()
                if not node_status:
                    logger.error(f"Failed to query node height to evaluate block time")
                    return

                node_height = int(node_status["sync_info"]["latest_block_height"])

                tasks = []
                for i in range(self.block_time_check_number):
                    tasks.append(session.get_block(height=node_height - i))
                result = await asyncio.gather(*tasks)
            converted_blocks_timestamp = []
            for block in result:
                if block:
                    block_timestamp = self.block_time_to_unix(
                        block["block"]["header"]["time"]
                    )
                    converted_blocks_timestamp.append(block_timestamp)
            if not converted_blocks_timestamp:
                logger.warning("Failed to update block time")
                return

            converted_blocks_timestamp.reverse()
            time_diffs = [
                converted_blocks_timestamp[i] - converted_blocks_timestamp[i - 1]
                for i in range(1, len(converted_blocks_timestamp))
            ]

            self.block_time = sum(time_diffs) / len(time_diffs)

            logger.info(f"Updated block time: {self.block_time}s")
        except Exception as e:
            logger.error(f"Failed to update block time: {e}")

    def update_upgrade_time(self):
        try:
            logger.debug(f"Calculating estimated upgrade time")

            blocks_left = self.upgrade_height - self.node_height
            _time_left_seconds = int(blocks_left * self.block_time)
            self.upgrade_time_left_seconds = _time_left_seconds
            logger.debug(f"Upgrade time left: {self.upgrade_time_left_seconds}")
        except Exception as e:
            logger.error(f"Failed to estimate upgrade time: {e}")

    async def update_consensus_state(self):
        try:
            async with AioHttpCalls(rpc=self.rpc, session=self.session) as session:
                data = await session.get_consensus_state()
            if not data:
                logger.error("Failed to fetch consensus_state. Will retry")
                return

            height_round_step = data["round_state"]["height/round/step"].split("/")
            _height = int(height_round_step[0])
            _round = int(height_round_step[1])
            _step = int(height_round_step[2])

            self.consensus_state["round"] = _round
            self.consensus_state["height"] = _height
            self.consensus_state["step"] = _step
            self.consensus_state["hash"] = data["round_state"]["proposal_block_hash"]
            self.consensus_state["proposer_hex"] = data["round_state"]["proposer"][
                "address"
            ]

            consensus = data["round_state"]["height_vote_set"][_round]

            _prevote_array = (
                float(consensus["prevotes_bit_array"].split("=")[-1].strip()) * 100
            )
            _precommits_array = (
                float(consensus["precommits_bit_array"].split("=")[-1].strip()) * 100
            )
            self.consensus_state["prevote_array"] = _prevote_array
            self.consensus_state["precommits_array"] = _precommits_array

            _online_precommit = 0
            _precommits_hex = {}
            for precommit in consensus["precommits"]:
                if "SIGNED_MSG_TYPE_PRECOMMIT(Precommit)" in precommit:
                    _online_precommit += 1
                    commit = precommit.split()[2]
                    hex = precommit.split()[0][-12:]
                    _precommits_hex[hex] = commit
                else:
                    continue
            self.consensus_state["hex_precommit"] = _precommits_hex

            _online_prevote = 0
            _prevotes_hex = {}
            for prevote in consensus["prevotes"]:
                if "SIGNED_MSG_TYPE_PREVOTE(Prevote)" in prevote:
                    _online_prevote += 1
                    vote = prevote.split()[2]
                    hex = prevote.split()[0][-12:]
                    _prevotes_hex[hex] = vote
                else:
                    continue
            self.consensus_state["hex_prevote"] = _prevotes_hex
            self.online_validators = (
                _online_prevote
                if _online_prevote > _online_precommit
                else _online_precommit
            )
            self.prevoting_validators = _online_prevote
            self.precommitting_validators = _online_precommit
            logger.info(f"Updated consensus state: {data['round_state']['height/round/step']}")

        except Exception as e:
            logger.error(
                f"An unexpected error occurred while processing consensus_state {e}"
            )
            return

    def create_progress_bar(
        self, label: str, value: float, _max: float = 100, is_percentage: bool = True
    ) -> str:

        value = max(0, min(value, _max))

        bar_length = 60
        filled_length = int(value * bar_length // _max)

        if self.disable_emojis:
            bar = "X" * filled_length + "-" * (bar_length - filled_length)
        else:
            bar = "█" * filled_length + "□" * (bar_length - filled_length)

        if is_percentage:
            return f"[bold cyan]{label}[/bold cyan] {bar} {value:.1f}%"
        else:
            return f"[bold cyan]{label}[/bold cyan] {bar} {value}/{_max}"

    def generate_table(self) -> Table:
        table = Table(show_lines=False, expand=True, box=None)
        table.add_column("", justify="left")
        table.add_column("", justify="left")
        table.add_column("", justify="left")

        column_data = [[] for _ in range(self.columns)]

        self.consensus_state["proposer_moniker"] = "N/A"
        for index, validator in enumerate(self.validators):
            column_index = index % self.columns
            moniker = validator["moniker"][:15].ljust(17)
            _hex_short = validator["hex"][:12]
            _voting_power = validator["vp"]

            if self.consensus_state["proposer_hex"] == validator["hex"]:
                self.consensus_state["proposer_moniker"] = validator["moniker"]

            _prevote = self.consensus_state["hex_prevote"].get(_hex_short)
            _precommit = self.consensus_state["hex_precommit"].get(_hex_short)
            _index_str = f"{index + 1}.".ljust(4)

            if self.hashes:
                _symbol = _prevote if _prevote else "[X]".ljust(12)
                _color = "bold green" if _prevote else "bold red"
                column_data[column_index].append(
                    f"{_index_str}{moniker}{str(f'{_voting_power:.1f}%').ljust(5)} [{_color}]{_symbol}[/{_color}]"
                )

            else:
                _positive_symbol = (
                    "[bold green][V][/bold green]" if self.disable_emojis else "✅"
                )
                _negative_symbol = (
                    "[bold red][X][/bold red]" if self.disable_emojis else "❌"
                )

                _first_symbol = _positive_symbol if _prevote else _negative_symbol
                _second_symbol = _positive_symbol if _precommit else _negative_symbol

                column_data[column_index].append(
                    f"{_index_str}{moniker}{str(f'{_voting_power:.1f}%').ljust(5)} {_first_symbol}{_second_symbol.ljust(10)}"
                )

        max_rows = max(len(col) for col in column_data)
        for row_index in range(max_rows):
            row = [
                (
                    column_data[col_index][row_index]
                    if row_index < len(column_data[col_index])
                    else ""
                )
                for col_index in range(self.columns)
            ]
            table.add_row(*row)

        return table

    async def start(self):
        try:
            self.dirty_network_info = True
            self.dirty_consensus_info = True

            with Live(
                self.layout,
                refresh_per_second=self.refresh_per_second,
                auto_refresh=True,
                screen=True,
            ) as live:

                while True:
                    log_renderable = self.rich_logger.get_logs()
                    log_panel = Panel(log_renderable, expand=True, box=box.SIMPLE)
                    self.layout["header"]["logs"].update(log_panel)

                    await self.batch_update_data()

                    new_node_status = {
                        "synced": self.synced,
                        "node_height": self.node_height,
                        "chain_id": self.chain_id,
                        "block_time": self.block_time,
                    }

                    new_upgrade_info = {
                        "upgrade_name": self.upgrade_name,
                        "upgrade_height": self.upgrade_height,
                    }
                    
                    if self.has_data_changed(new_node_status, self.previous_node_status) or self.has_data_changed(new_upgrade_info, self.previous_upgrade_info):
                        logger.debug(f"Node status or upgrade info has changed")

                        self.dirty_network_info = True
                        network_info = (
                            f"[bold cyan]Node Online & Synced:[/bold cyan] {self.synced}\n"
                            f"[bold cyan]Height:[/bold cyan] {self.node_height}\n"
                            f"[bold cyan]Chain ID:[/bold cyan] {self.chain_id}\n"
                        )
                        if self.block_time:
                            network_info += f"[bold cyan]Block time:[/bold cyan] {self.block_time:.4}s\n"

                            if self.upgrade_height and self.node_height:
                                self.update_upgrade_time()
                                blocks_left = self.upgrade_height - self.node_height
                                network_info += (
                                    f"[bold cyan]Upgrade:[/bold cyan] {self.upgrade_name} | "
                                    f"Height: {self.upgrade_height} | Blocks left: {blocks_left}"
                                )
                                if self.upgrade_time_left_seconds and self.upgrade_time_left_seconds >= 0:
                                    current_time_unix = int(datetime.now().replace(tzinfo=timezone.utc).timestamp())
                                    upgrade_time_unix = self.upgrade_time_left_seconds + current_time_unix

                                    time = datetime.fromtimestamp(upgrade_time_unix, timezone.utc)
                                    time_in_user_tz = time.astimezone(self.user_tz).strftime("%Y-%m-%d %H:%M:%S")
                                    network_info += f" | Time: {time_in_user_tz}"

                                    days, remainder = divmod(self.upgrade_time_left_seconds, 86400)
                                    hours, remainder = divmod(remainder, 3600)
                                    minutes, seconds = divmod(remainder, 60)

                                    parts = []
                                    if days > 0:
                                        parts.append(f"{days} day{'s' if days > 1 else ''}")
                                    if hours > 0:
                                        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
                                    if minutes > 0:
                                        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
                                    if seconds > 0 or not parts:
                                        parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")

                                    human_readable_countdown = "in " + " ".join(parts)

                                    network_info += f" ({human_readable_countdown})"
                                else:
                                    network_info += " (already occurred)"
                        
                        network_info_panel = Panel(
                            network_info,
                            title="Network Info",
                            border_style="blue_violet",
                            expand=True,
                        )
                        self.layout["header"]["network_info"].update(
                            network_info_panel
                        )
                        self.previous_node_status = new_node_status.copy()
                        self.previous_upgrade_info = new_upgrade_info.copy()

                    if self.has_data_changed(self.consensus_state, self.previous_consensus_state):
                        logger.debug(f"Consensus state has changed")

                        self.dirty_consensus_info = True
                        self.layout["main"].update(self.generate_table())

                        prevote_bar = self.create_progress_bar(
                            label="[ Prevotes ]",
                            value=self.consensus_state["prevote_array"],
                            _max=100,
                            is_percentage=True,
                        )

                        precommit_bar = self.create_progress_bar(
                            label="[Precommits]",
                            value=self.consensus_state["precommits_array"],
                            _max=100,
                            is_percentage=True,
                        )

                        step_bar = self.create_progress_bar(
                            label="[   Step   ]",
                            value=self.consensus_state["step"],
                            _max=6,
                            is_percentage=False,
                        )

                        votes_commits_renderable = (
                            f"{prevote_bar}\n{precommit_bar}\n{step_bar}"
                        )

                        votes_commits_panel = Panel(
                            votes_commits_renderable, box=box.SIMPLE, expand=True
                        )
                        self.layout["footer"].update(votes_commits_panel)

                        consensus_info = (
                            f"[bold cyan]Height/Round/Step:[/bold cyan] {self.consensus_state['height']}/"
                            f"{self.consensus_state['round']}/{self.consensus_state['step']}\n"
                            f"[bold cyan]Proposer:[/bold cyan] {self.consensus_state['proposer_moniker']}\n"
                            f"[bold cyan]Proposal Hash:[/bold cyan] {self.consensus_state['hash'] or 'N/A'}\n"
                            f"[bold cyan]Online validators:[/bold cyan] {self.online_validators} / {len(self.validators)}\n"
                            f"[bold cyan]Prevoting validators:[/bold cyan] {self.prevoting_validators}\n"
                            f"[bold cyan]Precommitting validators:[/bold cyan] {self.precommitting_validators}\n"
                        )
                        consensus_info_panel = Panel(
                            consensus_info,
                            title="Consensus Info",
                            border_style="green",
                            expand=True,
                        )
                        self.layout["header"]["consensus_info"].update(
                            consensus_info_panel
                        )
                        
                        self.previous_consensus_state = self.consensus_state.copy()

                    if any(
                        [
                            self.dirty_network_info,
                            self.dirty_consensus_info
                        ]
                    ):
                        # live.refresh()
                        self.dirty_network_info = False
                        self.dirty_consensus_info = False

        finally:
            await self.close_session()
            self.console.clear()

    # async def start(self):
    #     try:

    #         with Live(
    #             self.layout,
    #             refresh_per_second=self.refresh_per_second,
    #             auto_refresh=True,
    #             screen=True,
    #         ) as live:

    #             while True:
    #                 log_renderable = self.rich_logger.get_logs()
    #                 log_panel = Panel(log_renderable, expand=True, box=box.SIMPLE)
    #                 self.layout["header"]["logs"].update(log_panel)

    #                 await self.batch_update_data()

    #                 new_node_status = {
    #                     "synced": self.synced,
    #                     "node_height": self.node_height,
    #                     "chain_id": self.chain_id,
    #                     "block_time": self.block_time,
    #                 }

    #                 new_upgrade_info = {
    #                     "upgrade_name": self.upgrade_name,
    #                     "upgrade_height": self.upgrade_height,
    #                 }

    #                 self.dirty_network_info = True
    #                 network_info = (
    #                     f"[bold cyan]Node Online & Synced:[/bold cyan] {self.synced}\n"
    #                     f"[bold cyan]Height:[/bold cyan] {self.node_height}\n"
    #                     f"[bold cyan]Chain ID:[/bold cyan] {self.chain_id}\n"
    #                 )
    #                 if self.block_time:
    #                     network_info += f"[bold cyan]Block time:[/bold cyan] {self.block_time:.4}s\n"

    #                     if self.upgrade_height and self.node_height:
    #                         self.update_upgrade_time()
    #                         blocks_left = self.upgrade_height - self.node_height
    #                         network_info += (
    #                             f"[bold cyan]Upgrade:[/bold cyan] {self.upgrade_name} | "
    #                             f"Height: {self.upgrade_height} | Blocks left: {blocks_left}"
    #                         )
    #                         if self.upgrade_time_left_seconds and self.upgrade_time_left_seconds >= 0:
    #                             current_time_unix = int(datetime.now().replace(tzinfo=timezone.utc).timestamp())
    #                             upgrade_time_unix = self.upgrade_time_left_seconds + current_time_unix

    #                             time = datetime.fromtimestamp(upgrade_time_unix, timezone.utc)
    #                             time_in_user_tz = time.astimezone(self.user_tz).strftime("%Y-%m-%d %H:%M:%S")
    #                             network_info += f" | Time: {time_in_user_tz}"

    #                             days, remainder = divmod(self.upgrade_time_left_seconds, 86400)
    #                             hours, remainder = divmod(remainder, 3600)
    #                             minutes, seconds = divmod(remainder, 60)

    #                             parts = []
    #                             if days > 0:
    #                                 parts.append(f"{days} day{'s' if days > 1 else ''}")
    #                             if hours > 0:
    #                                 parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    #                             if minutes > 0:
    #                                 parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    #                             if seconds > 0 or not parts:
    #                                 parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")

    #                             human_readable_countdown = "in " + " ".join(parts)

    #                             network_info += f" ({human_readable_countdown})"
    #                         else:
    #                             network_info += " (already occurred)"
                    
    #                 network_info_panel = Panel(
    #                     network_info,
    #                     title="Network Info",
    #                     border_style="blue_violet",
    #                     expand=True,
    #                 )
    #                 self.layout["header"]["network_info"].update(
    #                     network_info_panel
    #                 )
    #                 self.previous_node_status = new_node_status.copy()
    #                 self.previous_upgrade_info = new_upgrade_info.copy()

    #                 logger.debug(f"Consensus state has changed")

    #                 self.dirty_consensus_info = True
    #                 self.layout["main"].update(self.generate_table())

    #                 prevote_bar = self.create_progress_bar(
    #                     label="[ Prevotes ]",
    #                     value=self.consensus_state["prevote_array"],
    #                     _max=100,
    #                     is_percentage=True,
    #                 )

    #                 precommit_bar = self.create_progress_bar(
    #                     label="[Precommits]",
    #                     value=self.consensus_state["precommits_array"],
    #                     _max=100,
    #                     is_percentage=True,
    #                 )

    #                 step_bar = self.create_progress_bar(
    #                     label="[   Step   ]",
    #                     value=self.consensus_state["step"],
    #                     _max=6,
    #                     is_percentage=False,
    #                 )

    #                 votes_commits_renderable = (
    #                     f"{prevote_bar}\n{precommit_bar}\n{step_bar}"
    #                 )

    #                 votes_commits_panel = Panel(
    #                     votes_commits_renderable, box=box.SIMPLE, expand=True
    #                 )
    #                 self.layout["footer"].update(votes_commits_panel)

    #                 consensus_info = (
    #                     f"[bold cyan]Height/Round/Step:[/bold cyan] {self.consensus_state['height']}/"
    #                     f"{self.consensus_state['round']}/{self.consensus_state['step']}\n"
    #                     f"[bold cyan]Proposer:[/bold cyan] {self.consensus_state['proposer_moniker']}\n"
    #                     f"[bold cyan]Proposal Hash:[/bold cyan] {self.consensus_state['hash'] or 'N/A'}\n"
    #                     f"[bold cyan]Online validators:[/bold cyan] {self.online_validators} / {len(self.validators)}\n"
    #                     f"[bold cyan]Prevoting validators:[/bold cyan] {self.prevoting_validators}\n"
    #                     f"[bold cyan]Precommitting validators:[/bold cyan] {self.precommitting_validators}\n"
    #                 )
    #                 consensus_info_panel = Panel(
    #                     consensus_info,
    #                     title="Consensus Info",
    #                     border_style="green",
    #                     expand=True,
    #                 )
    #                 self.layout["header"]["consensus_info"].update(
    #                     consensus_info_panel
    #                 )
                    
    #                 self.previous_consensus_state = self.consensus_state.copy()

    #                 # live.refresh()


    #     finally:
    #         await self.close_session()
    #         self.console.clear()

    async def update_data(
        self, update_func: Callable, refresh_interval: int, last_update_time_attr: str
    ) -> None:
        """
        A helper function to periodically call an update method.

        Args:
            update_func (Callable): The asynchronous function to call for updates.
            refresh_interval (int): The interval (in seconds) to wait before calling the update function again.
            last_update_time_attr (str): The attribute name to store the last update time.

        Returns:
            None
        """
        if (
            not hasattr(self, last_update_time_attr)
            or (asyncio.get_event_loop().time() - getattr(self, last_update_time_attr))
            >= refresh_interval
        ):
            await update_func()
            setattr(self, last_update_time_attr, asyncio.get_event_loop().time())

    async def batch_update_data(self):
        """
        A method to perform batch updates by calling multiple update functions concurrently.

        This method uses `asyncio.gather` to execute several `update_data` calls in parallel,
        ensuring that each update method is executed within its respective refresh interval.
        Each individual update method is wrapped by the `update_data` helper function, which
        handles refresh timing and stores the last update timestamp.

        Updates included:
            - Node status update
            - Validators update
            - Consensus state update
            - Block time update
            - Upgrade plan update

        Args:
            None

        Returns:
            None
        """
        await asyncio.gather(
            self.update_data(
                self.update_node_status, self.refresh_node, "_last_node_status_update"
            ),
            self.update_data(
                self.set_genesis_validators if self.genesis is not None else self.update_validators,
                self.refresh_validators,
                "_last_validators_update",
            ),
            self.update_data(
                self.update_consensus_state,
                self.refresh_consensus_state,
                "_last_consensus_update",
            ),
            self.update_data(
                self.update_block_time,
                self.refresh_block_time,
                "_last_block_time_update",
            ),
            self.update_data(
                self.update_upgrade, self.refresh_upgrade_plan, "_last_upgrade_update"
            ),
        )

    def has_data_changed(self, new_data: dict, old_data: dict) -> bool:
        """
        Check if the new data is different from the old data.

        Args:
            new_data (dict): The updated data to compare.
            old_data (dict): The previous data for comparison.

        Returns:
            bool: True if data has changed, False otherwise.
        """
        return new_data != old_data

    async def close_session(self):
        """
        Closes the shared aiohttp ClientSession.

        This method ensures that the shared ClientSession is properly closed, releasing
        all resources associated with it. It should be called when the ConsensusDashboard
        is no longer needed, such as during application shutdown, to prevent resource leaks.

        Args:
            None

        Returns:
            None
        """
        await self.session.close()
