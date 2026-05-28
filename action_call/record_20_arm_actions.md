# Record 20 Arm Actions

先启动 daemon，并保持录制模式使用 `disabled`。每条命令都在 `/home/lww/reachy_mini` 下执行。

```bash
cd /home/lww/reachy_mini
```

| # | clip_id | description | command |
|---:|---|---|---|
| 1 | `open_welcome` | 双臂从身体两侧打开，像欢迎。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id open_welcome --label "Open welcome" --overwrite` |
| 2 | `come_here` | 双臂向外后再向身体方向收回，像邀请靠近。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id come_here --label "Come here" --overwrite` |
| 3 | `single_wave` | 单侧手臂直臂左右轻摆，像挥手。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id single_wave --label "Single wave" --overwrite` |
| 4 | `double_wave` | 双臂同时小幅左右摆，轻松友好。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id double_wave --label "Double wave" --overwrite` |
| 5 | `arms_raise` | 双臂快速抬高，表达惊讶。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id arms_raise --label "Arms raise" --overwrite` |
| 6 | `victory_lift` | 双臂向上举起一次，表达成功。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id victory_lift --label "Victory lift" --overwrite` |
| 7 | `excited_bounce` | 双臂小幅上下连续摆动，表达兴奋。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id excited_bounce --label "Excited bounce" --overwrite` |
| 8 | `dance_swing` | 双臂左右交替摆动，像简单跳舞。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id dance_swing --label "Dance swing" --overwrite` |
| 9 | `calm_down` | 双臂从较高位置缓慢下压，表达安抚。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id calm_down --label "Calm down" --overwrite` |
| 10 | `question_shrug` | 双臂微微向外展开，像疑问/不确定。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id question_shrug --label "Question shrug" --overwrite` |
| 11 | `thinking_pose` | 一侧手臂稍抬，另一侧低位，保持思考姿态。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id thinking_pose --label "Thinking pose" --overwrite` |
| 12 | `understanding_open` | 双臂轻微向前打开，表达理解/同意。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id understanding_open --label "Understanding open" --overwrite` |
| 13 | `sad_drop` | 双臂缓慢下垂，表达难过。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id sad_drop --label "Sad drop" --overwrite` |
| 14 | `tired_slump` | 双臂沉重下垂并轻微松弛，表达疲惫。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id tired_slump --label "Tired slump" --overwrite` |
| 15 | `shy_cover` | 双臂向身体前方内收，表达害羞/尴尬。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id shy_cover --label "Shy cover" --overwrite` |
| 16 | `fear_guard` | 双臂抬到身体前方，像防御。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id fear_guard --label "Fear guard" --overwrite` |
| 17 | `push_away` | 双臂向外推出，表达拒绝/远离。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id push_away --label "Push away" --overwrite` |
| 18 | `no_cross` | 双臂交叉或横向摆动，表达否定。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id no_cross --label "No cross" --overwrite` |
| 19 | `angry_slam` | 双臂快速向下压，表达愤怒。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id angry_slam --label "Angry slam" --overwrite` |
| 20 | `oops_flinch` | 双臂短促后缩/抬起再回落，表达失误。 | `/home/tzhx/miniconda3/envs/robot/bin/python action_pipeline/record_arm_clip.py --clip-id oops_flinch --label "Oops flinch" --overwrite` |

录完后检查数量：

```bash
find action_pipeline/arm_clips -maxdepth 1 -name '*.json' | sort
```
