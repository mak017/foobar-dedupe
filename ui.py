import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Dict, Any, Callable
import os

from matcher import TrackCluster, CandidateMatch, format_duration
from trash import batch_delete_to_trash, sync_foobar_playlist


class DedupeReviewApp:
    def __init__(self, root: tk.Tk, clusters: List[TrackCluster], on_complete: Callable = None):
        self.root = root
        self.clusters = clusters
        self.on_complete = on_complete

        self.root.title("foobar2000 Duplicate & Upgrade Assistant")
        self.root.geometry("1100x720")
        self.root.minsize(850, 500)

        # Style colors (Dark theme to match foobar DarkOne)
        self.bg_main = "#1e1e1e"
        self.bg_panel = "#252526"
        self.bg_card = "#2d2d30"
        self.bg_card_hover = "#37373d"
        self.fg_text = "#e0e0e0"
        self.fg_muted = "#9da1a6"
        self.color_accent = "#007acc"
        self.color_green = "#4ec9b0"
        self.color_orange = "#ce9178"
        self.color_yellow = "#dcdcaa"
        self.color_red = "#f44747"

        self.root.configure(bg=self.bg_main)

        # Map of checkbox_var -> file_path_to_delete
        self.checkbox_vars = {}
        self.items_data = []

        self._setup_ui()
        self._populate_clusters()
        self._update_summary()

    def _setup_ui(self):
        # Top Header Frame
        header_frame = tk.Frame(self.root, bg=self.bg_panel, height=60, padx=20, pady=12)
        header_frame.pack(fill=tk.X, side=tk.TOP)

        title_lbl = tk.Label(
            header_frame,
            text="Duplicate & Upgrade Review",
            font=("Segoe UI", 14, "bold"),
            bg=self.bg_panel,
            fg=self.fg_text
        )
        title_lbl.pack(side=tk.LEFT)

        total_matches = sum(len(c.matches) for c in self.clusters)
        self.sub_lbl = tk.Label(
            header_frame,
            text=f"{len(self.clusters)} incoming track(s) analyzed | {total_matches} candidate match(es)",
            font=("Segoe UI", 10),
            bg=self.bg_panel,
            fg=self.fg_muted
        )
        self.sub_lbl.pack(side=tk.LEFT, padx=(15, 0), pady=(3, 0))

        # Bottom Action Bar
        bottom_frame = tk.Frame(self.root, bg=self.bg_panel, height=65, padx=20, pady=12)
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        btn_style = {"font": ("Segoe UI", 9), "relief": "flat", "padx": 12, "pady": 6, "cursor": "hand2"}

        btn_all = tk.Button(
            bottom_frame,
            text="Select Safe Recommendations",
            bg="#3a3d41",
            fg=self.fg_text,
            command=self._select_recommended,
            **btn_style
        )
        btn_all.pack(side=tk.LEFT, padx=(0, 8))

        btn_none = tk.Button(
            bottom_frame,
            text="Deselect All",
            bg="#3a3d41",
            fg=self.fg_text,
            command=self._deselect_all,
            **btn_style
        )
        btn_none.pack(side=tk.LEFT)

        # Right buttons
        self.btn_confirm = tk.Button(
            bottom_frame,
            text="Confirm & Move to Recycle Bin (0)",
            bg=self.color_accent,
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=16,
            pady=6,
            cursor="hand2",
            command=self._execute_actions
        )
        self.btn_confirm.pack(side=tk.RIGHT, padx=(10, 0))

        btn_cancel = tk.Button(
            bottom_frame,
            text="Cancel",
            bg="#3a3d41",
            fg=self.fg_text,
            command=self.root.destroy,
            **btn_style
        )
        btn_cancel.pack(side=tk.RIGHT)

        self.summary_lbl = tk.Label(
            bottom_frame,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg=self.bg_panel,
            fg=self.color_green
        )
        self.summary_lbl.pack(side=tk.RIGHT, padx=20)

        # Center Scrollable Area
        center_frame = tk.Frame(self.root, bg=self.bg_main)
        center_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        self.canvas = tk.Canvas(center_frame, bg=self.bg_main, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(center_frame, orient="vertical", command=self.canvas.yview)
        self.scroll_content = tk.Frame(self.canvas, bg=self.bg_main)

        self.scroll_content.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scroll_content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.bind('<Configure>', self._on_canvas_configure)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Enable mousewheel scrolling
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _populate_clusters(self):
        if not self.clusters:
            no_match_frame = tk.Frame(self.scroll_content, bg=self.bg_card, padx=30, pady=30)
            no_match_frame.pack(fill=tk.X, pady=20)
            tk.Label(
                no_match_frame,
                text="No duplicates or upgrade candidates found among selected tracks.",
                font=("Segoe UI", 11),
                bg=self.bg_card,
                fg=self.color_green
            ).pack()
            self.btn_confirm.configure(state=tk.DISABLED)
            return

        for idx, cluster in enumerate(self.clusters, 1):
            inc = cluster.incoming
            inc_dur = format_duration(inc['duration'])
            inc_q = f"{inc['format']} {inc['bitrate']}k"

            # Cluster Box
            cluster_frame = tk.Frame(self.scroll_content, bg=self.bg_panel, relief="flat", bd=1)
            cluster_frame.pack(fill=tk.X, pady=(0, 15))

            # Incoming Track Header
            inc_header = tk.Frame(cluster_frame, bg="#2d3139", padx=12, pady=10)
            inc_header.pack(fill=tk.X)

            tag_inc = tk.Label(
                inc_header,
                text=" INCOMING TRACK ",
                font=("Segoe UI", 8, "bold"),
                bg="#3e4451",
                fg="#abb2bf",
                padx=6,
                pady=2
            )
            tag_inc.pack(side=tk.LEFT, padx=(0, 10))

            inc_title = f"{inc['artist']} - {inc['title']}"
            if inc.get('mix_name') and inc['mix_name'] not in inc['title']:
                inc_title += f" ({inc['mix_name']})"

            lbl_title = tk.Label(
                inc_header,
                text=inc_title,
                font=("Segoe UI", 10, "bold"),
                bg="#2d3139",
                fg=self.fg_text
            )
            lbl_title.pack(side=tk.LEFT)

            inc_meta = f"[{inc_dur} | {inc_q}]"
            lbl_meta = tk.Label(
                inc_header,
                text=inc_meta,
                font=("Segoe UI", 9),
                bg="#2d3139",
                fg=self.color_green
            )
            lbl_meta.pack(side=tk.LEFT, padx=(8, 0))

            lbl_path = tk.Label(
                cluster_frame,
                text=f"Location: {inc['path']}",
                font=("Segoe UI", 8),
                bg=self.bg_panel,
                fg=self.fg_muted,
                padx=15,
                pady=4,
                anchor="w"
            )
            lbl_path.pack(fill=tk.X)

            # Matches container
            matches_frame = tk.Frame(cluster_frame, bg=self.bg_panel, padx=12, pady=6)
            matches_frame.pack(fill=tk.X)

            for m_idx, match in enumerate(cluster.matches):
                cand = match.candidate
                cand_dur = format_duration(cand['duration'])
                cand_q = f"{cand['format']} {cand['bitrate']}k"

                card_bg = self.bg_card
                card = tk.Frame(matches_frame, bg=card_bg, padx=12, pady=8, bd=1, relief="solid")
                card.configure(highlightbackground="#3e3e42", highlightthickness=1)
                card.pack(fill=tk.X, pady=(0, 6))

                # Checkbox & Status
                top_row = tk.Frame(card, bg=card_bg)
                top_row.pack(fill=tk.X)

                var = tk.BooleanVar(value=match.recommended_check)
                cb = tk.Checkbutton(
                    top_row,
                    variable=var,
                    bg=card_bg,
                    activebackground=card_bg,
                    selectcolor="#1e1e1e",
                    command=self._update_summary,
                    cursor="hand2"
                )
                cb.pack(side=tk.LEFT, padx=(0, 6))

                # Status Badge color
                badge_bg = "#3e3e42"
                badge_fg = "#ffffff"
                if "UPGRADE" in match.status:
                    badge_bg = "#1b442b"
                    badge_fg = self.color_green
                elif "INFERIOR" in match.status or "DUPLICATE" in match.status:
                    badge_bg = "#4d2323"
                    badge_fg = self.color_red
                elif "DIFFERENT" in match.status:
                    badge_bg = "#473b18"
                    badge_fg = self.color_yellow

                badge = tk.Label(
                    top_row,
                    text=f" {match.status_label} ",
                    font=("Segoe UI", 8, "bold"),
                    bg=badge_bg,
                    fg=badge_fg,
                    padx=6,
                    pady=1
                )
                badge.pack(side=tk.LEFT, padx=(0, 10))

                cand_title = f"{cand['artist']} - {cand['title']}"
                if cand.get('mix_name') and cand['mix_name'] not in cand['title']:
                    cand_title += f" ({cand['mix_name']})"

                lbl_cand = tk.Label(
                    top_row,
                    text=f"Library Match: {cand_title} [{cand_dur} | {cand_q}]",
                    font=("Segoe UI", 9, "bold" if "UPGRADE" in match.status else "normal"),
                    bg=card_bg,
                    fg=self.fg_text
                )
                lbl_cand.pack(side=tk.LEFT)

                # Reason & Target path
                info_row = tk.Frame(card, bg=card_bg)
                info_row.pack(fill=tk.X, pady=(4, 0), padx=(26, 0))

                target_txt = f"Action: Move to Trash -> {match.target_path}" if match.target_path else "Action: Keep Both"
                lbl_reason = tk.Label(
                    info_row,
                    text=f"• {match.reason}\n• {target_txt}",
                    font=("Segoe UI", 8),
                    bg=card_bg,
                    fg=self.fg_muted,
                    justify="left",
                    anchor="w"
                )
                lbl_reason.pack(fill=tk.X)

                if match.target_path:
                    self.checkbox_vars[var] = match.target_path
                    self.items_data.append((var, match))

    def _select_recommended(self):
        for var, match in self.items_data:
            var.set(match.recommended_check)
        self._update_summary()

    def _deselect_all(self):
        for var in self.checkbox_vars.keys():
            var.set(False)
        self._update_summary()

    def _update_summary(self):
        selected_count = sum(1 for var in self.checkbox_vars.keys() if var.get())
        self.btn_confirm.configure(
            text=f"Confirm & Move to Recycle Bin ({selected_count})",
            state=tk.NORMAL if selected_count > 0 else tk.DISABLED
        )
        self.summary_lbl.configure(text=f"{selected_count} file(s) selected for deletion")

    def _execute_actions(self):
        files_to_delete = [path for var, path in self.checkbox_vars.items() if var.get()]
        if not files_to_delete:
            return

        confirm = messagebox.askyesno(
            "Confirm Safe Deletion",
            f"Are you sure you want to move {len(files_to_delete)} file(s) to the Windows Recycle Bin?\n\n"
            "• Files can be restored from the Recycle Bin if needed.\n"
            "• foobar2000 will be signaled to purge removed items from your playlist.\n"
            "• The library database index will be updated.",
            parent=self.root
        )

        if not confirm:
            return

        success, failed = batch_delete_to_trash(files_to_delete)
        sync_foobar_playlist()

        msg = f"Successfully moved {len(success)} file(s) to the Recycle Bin."
        if failed:
            msg += f"\nFailed to delete {len(failed)} file(s)."

        messagebox.showinfo("Operation Complete", msg, parent=self.root)

        if self.on_complete:
            self.on_complete()

        self.root.destroy()


def run_review_ui(clusters: List[TrackCluster], on_complete: Callable = None):
    """Entry point to launch the Tkinter GUI."""
    root = tk.Tk()
    app = DedupeReviewApp(root, clusters, on_complete)
    root.mainloop()
