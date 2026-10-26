# Damage dataset: data dictionary

Rows: 124 (90 with a label: replay_ok = 1). One row per moved elephant and event: a deliberate reroute of the Chunk 5 design (source = design) or a controller's own move in Chunk 4, replayed (source = chunk4). [ctl] = controller-visible at decision time (model input); [derived] = computed from [ctl] columns (controllers/cost.derive); [host] = sender-side ss, analysis only; [design] = the trial's factors; [label] = measured from the matched move / no-move pair (C minus T, positive = the move cost goodput).

| column | meaning |
|---|---|
| live_run | live run id (campaign runs/<id>) |
| controller | controller that made the move |
| seed | traffic seed of the live run |
| group | split group for leave-seed-out CV (= seed) |
| round | controller poll round of the event |
| t_event_s | event time, s since t0 (live) |
| flow | moved elephant id |
| from_spine | spine left |
| to_spine | destination spine |
| n_moved_in_event | elephants moved in the same round (one batch) |
| n_bystanders | elephants on the destination spine after the event, not moved in it |
| rate_mbps | [ctl] moved flow rate, last 1 s interval |
| U_p | [ctl] load of the source uplink, Mbit/s |
| U_q | [ctl] load of the destination uplink |
| gain_mbps | [ctl] U_p - (U_q + rate) |
| elephants_on_p | [ctl] elephants on the source path |
| elephants_on_q | [ctl] elephants on the destination path |
| age_s | [ctl] time since the flow's SYN |
| bytes_so_far | [ctl] bytes counted at the ingress leaf |
| n_prev_moves | [ctl] earlier moves of this flow |
| since_last_move_s | [ctl] s since its last move (empty = never) |
| delay_ms_p_config | [ctl] configured one-way delay of the source spine |
| delay_ms_q_config | [ctl] configured one-way delay of the destination spine |
| delay_gap_ms | [derived] q delay - p delay (negative = slow->fast) |
| rate_share_q | [derived] rate / (U_q + rate) |
| bytes_in_flight_est | [derived] rate x 2 x delay_p |
| cwnd_before | [host] moved flow cwnd (segments), median over 0.5 s before the move (ss) |
| unacked_before | [host] moved flow unacked segments before the move (bytes in flight) |
| rtt_before_ms | [host] moved flow srtt before the move |
| bystander_cwnd_before | [host] median cwnd of the bystanders before the move |
| cwnd_asymmetry | [host] cwnd_before / bystander_cwnd_before (cwnd-at-arrival asymmetry) |
| replay_ok | 1 if both T and C runs exist without errors |
| moved_deficit_W2 | [label] bytes, G_C - G_T of the moved flow in 2 s |
| bystander_deficit_W2 | [label] bytes, sum over bystanders |
| dest_total_deficit_W2 | [label] moved + bystander: the damage number |
| moved_G_T_W2 | moved flow goodput in T, Mbit/s |
| moved_G_C_W2 | moved flow goodput in C, Mbit/s |
| d2_TcpRetransSegs | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPDSACKRecv | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPSACKReorder | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPTSReorder | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPSpuriousRTOs | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPTimeouts | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPLossUndo | [label] sender-host nstat delta over 2 s, T minus C |
| d2_TcpExtTCPDSACKUndo | [label] sender-host nstat delta over 2 s, T minus C |
| moved_deficit_W5 | [label] bytes, G_C - G_T of the moved flow in 5 s |
| bystander_deficit_W5 | [label] bytes, sum over bystanders |
| dest_total_deficit_W5 | [label] moved + bystander: the damage number |
| moved_G_T_W5 | moved flow goodput in T, Mbit/s |
| moved_G_C_W5 | moved flow goodput in C, Mbit/s |
| d5_TcpRetransSegs | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPDSACKRecv | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPSACKReorder | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPTSReorder | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPSpuriousRTOs | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPTimeouts | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPLossUndo | [label] sender-host nstat delta over 5 s, T minus C |
| d5_TcpExtTCPDSACKUndo | [label] sender-host nstat delta over 5 s, T minus C |
| moved_deficit_W10 | [label] bytes, G_C - G_T of the moved flow in 10 s |
| bystander_deficit_W10 | [label] bytes, sum over bystanders |
| dest_total_deficit_W10 | [label] moved + bystander: the damage number |
| moved_G_T_W10 | moved flow goodput in T, Mbit/s |
| moved_G_C_W10 | moved flow goodput in C, Mbit/s |
| d10_TcpRetransSegs | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPDSACKRecv | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPSACKReorder | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPTSReorder | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPSpuriousRTOs | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPTimeouts | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPLossUndo | [label] sender-host nstat delta over 10 s, T minus C |
| d10_TcpExtTCPDSACKUndo | [label] sender-host nstat delta over 10 s, T minus C |
| source | design (Chunk 5 deliberate reroute) or chunk4 (a controller's own move, replayed) |
| scenario | direction (slow->fast, fast->slow, same) | q<elephants already on the destination> |
| pair | source>destination spine; held-out group of the design rows (group column) |
| trial | design trial (experiments/damage_design.py), runs t<trial>-T and -C |
| campaign | the design campaign directory the row comes from |
| cap_mode | [design] how the cap was applied: b = iperf3 -b (an average, catches up), fq = --fq-rate pacing |
| excluded | why the row has no label (empty = analysed) |
| n_p_design | [design] elephants other than the moved one on the source |
| n_q_design | [design] elephants on the destination (bystanders) |
| cap | [design] iperf3 bitrate cap of the moved flow (empty = none) |
| mice | [design] 1 = Poisson mice from h2..h4 |
| flap | [design] 1 = the previous move came from the destination |
| ctl_round_lag_s | [ctl] age of the monitor round the features come from (s before the move) |
| ctl_path_ok | 1 if the monitor saw the moved flow on its source spine in that round |
| t_move_T_s | move arm: barrier reply of the move, s since t0 (window start) |
| t_move_C_s | no-move arm: barrier reply of the sham, s since t0 (window start) |
| max_start_lag_s | largest delay of an elephant's first packet at its receiver behind its scheduled start, either arm |
| left_behind_deficit_W2 | [label] bytes, sum over elephants left on the source |
| all_total_deficit_W2 | [label] bytes, sum over all four elephants |
| left_behind_deficit_W5 | [label] bytes, sum over elephants left on the source |
| all_total_deficit_W5 | [label] bytes, sum over all four elephants |
| left_behind_deficit_W10 | [label] bytes, sum over elephants left on the source |
| all_total_deficit_W10 | [label] bytes, sum over all four elephants |
| dest_total_deficit_pre | bytes in the 2 s before the move, C - T (arms differ by construction) |
| ss_retrans_W2_T | [label] moved flow's own socket (ss): retrans over 2 s after the move, arm T |
| ss_retrans_W2_C | [label] moved flow's own socket (ss): retrans over 2 s after the move, arm C |
| ss_reord_seen_W2_T | [label] moved flow's own socket (ss): reord_seen over 2 s after the move, arm T |
| ss_reord_seen_W2_C | [label] moved flow's own socket (ss): reord_seen over 2 s after the move, arm C |
| ss_dsack_dups_W2_T | [label] moved flow's own socket (ss): dsack_dups over 2 s after the move, arm T |
| ss_dsack_dups_W2_C | [label] moved flow's own socket (ss): dsack_dups over 2 s after the move, arm C |
| ss_retrans_W10_T | [label] moved flow's own socket (ss): retrans over 10 s after the move, arm T |
| ss_retrans_W10_C | [label] moved flow's own socket (ss): retrans over 10 s after the move, arm C |
| ss_reord_seen_W10_T | [label] moved flow's own socket (ss): reord_seen over 10 s after the move, arm T |
| ss_reord_seen_W10_C | [label] moved flow's own socket (ss): reord_seen over 10 s after the move, arm C |
| ss_dsack_dups_W10_T | [label] moved flow's own socket (ss): dsack_dups over 10 s after the move, arm T |
| ss_dsack_dups_W10_C | [label] moved flow's own socket (ss): dsack_dups over 10 s after the move, arm C |
