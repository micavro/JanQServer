using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using BepInEx;
using HarmonyLib;
using Janq;
using Newtonsoft.Json;
using SphingoAPI;

namespace JanqProbe;

[BepInPlugin("janq.lab.probe", "JanQ Probe", "0.2.0")]
public sealed class Plugin : BaseUnityPlugin
{
    private void Awake()
    {
        ProbeLog.Initialize(Logger);
        UnityEngine.Application.runInBackground = true;
        ActionBridge.Initialize(Paths.GameRootPath);
        AccountPrepBridge.Initialize(Paths.GameRootPath);
        var harmony = new Harmony("janq.lab.probe");
        harmony.PatchAll(typeof(Plugin).Assembly);
        ActionBridgeRunner.Ensure();
        AccountPrepRunner.Ensure();
        ProbeLog.Write("probe_loaded", new
        {
            version = "0.2.0",
            gameRoot = Paths.GameRootPath,
            logPath = ProbeLog.LogPath,
            bridgeRoot = ActionBridge.RootPath,
            runInBackground = UnityEngine.Application.runInBackground
        });
        Logger.LogInfo($"JanQ Probe loaded; logging to {ProbeLog.LogPath}");
    }

    private void OnDestroy()
    {
        ProbeLog.Write("plugin_host_destroyed", new { bridgeContinues = true });
    }
}

internal static class ProbeLog
{
    private static readonly object Sync = new();
    private static BepInEx.Logging.ManualLogSource? logger;

    public static string LogPath { get; private set; } = "";

    public static void Initialize(BepInEx.Logging.ManualLogSource source)
    {
        logger = source;
        LogPath = ResolveLogPath();
        Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
    }

    public static void Write(string type, object payload)
    {
        try
        {
            var envelope = new
            {
                ts = DateTimeOffset.UtcNow.ToString("O"),
                type,
                payload
            };
            var line = JsonConvert.SerializeObject(envelope);
            lock (Sync)
            {
                File.AppendAllText(LogPath, line + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch (Exception ex)
        {
            logger?.LogError($"JanQ Probe logging failed: {ex}");
        }
    }

    private static string ResolveLogPath()
    {
        var env = Environment.GetEnvironmentVariable("JANQ_PROBE_LOG");
        if (!string.IsNullOrWhiteSpace(env))
        {
            return Path.GetFullPath(env);
        }

        var workspace = Path.GetFullPath(Path.Combine(Paths.GameRootPath, "..", ".."));
        return Path.Combine(workspace, "_runtime", "logs", "janq_events.jsonl");
    }
}

internal static class Projection
{
    public static object? YakuOddsList(JanQAPI.spYakuOddsList? data)
    {
        if (data == null)
        {
            return null;
        }

        var odds = new List<object>();
        foreach (var item in data.oddsList)
        {
            odds.Add(new
            {
                level = item.level.ToString(),
                levelId = (int)item.level,
                item.odds
            });
        }

        return new { odds };
    }

    public static object? Haipai(JanQAPI.spGameHaipai? data)
    {
        if (data == null)
        {
            return null;
        }

        return new
        {
            data.gold,
            data.mjchip,
            data.cchip,
            data.level,
            data.omoDora,
            data.uraDora,
            data.zandan,
            status = data.status.ToString(),
            statusId = (int)data.status,
            data.tenhou,
            data.tsumo,
            haipai = data.haipai
        };
    }

    public static object? Tsumo(JanQAPI.spGameTsumo? data)
    {
        if (data == null)
        {
            return null;
        }

        return new
        {
            data.gold,
            data.mjchip,
            data.cchip,
            data.level,
            data.pai,
            data.zandan,
            status = data.status.ToString(),
            statusId = (int)data.status,
            data.richi,
            data.replay,
            data.agari,
            tehai = data.tehai,
            data.omo_dora,
            data.ura_dora
        };
    }

    public static object? Dahai(JanQAPI.spPlayerDahai? data)
    {
        if (data == null)
        {
            return null;
        }

        return new
        {
            data.richi,
            data.pos,
            data.sutehai
        };
    }

    public static object? Result(JanQAPI.spJanQResult? data)
    {
        if (data == null)
        {
            return null;
        }

        return new
        {
            data.gold,
            data.mjchip,
            data.cchip,
            data.level,
            nextMode = data.nextMode.ToString(),
            nextModeId = (int)data.nextMode,
            status = data.status.ToString(),
            statusId = (int)data.status,
            data.yakuman,
            data.han,
            yakuLevel = data.yakuLevel.ToString(),
            yakuLevelId = (int)data.yakuLevel,
            data.nbOmoDora,
            data.nbUraDora,
            data.win,
            data.last_win,
            data.renchan,
            data.odds,
            tehai = data.tehai,
            yaku = data.yaku,
            data.yakuman_count,
            data.yakuman_renchan
        };
    }
}

internal static class GameManagerProjection
{
    private const int BlankTile = 9999;

    public static object Snapshot(GameManager instance, string trigger)
    {
        var mode = FieldText(instance, "mGameMode");
        return new
        {
            trigger,
            gameMode = mode,
            gameModeRequest = FieldText(instance, "mGameModeRequest"),
            gameModeNext = FieldText(instance, "mGameModeNext"),
            state = CurrentState(instance, mode),
            requestState = CurrentRequestState(instance, mode),
            mainButtonType = FieldText(instance, "mMainButtonType"),
            mainButtonRequest = FieldText(instance, "mMainButtonRequest"),
            mainButtonPushType = FieldText(instance, "mMainButtonPushType"),
            betRate = FieldInt(instance, "mBets"),
            balls = FieldInt(instance, "mBalls"),
            isReach = FieldBool(instance, "mIsReach"),
            shantenNum = FieldInt(instance, "mShantenNum"),
            shotArea = FieldInt(instance, "mShotArea"),
            paiSuteIndex = FieldInt(instance, "mPaiSuteIndex"),
            paiUpSelectIndex = FieldInt(instance, "mPaiUpSelectIndex"),
            dora = PaiId(FieldValue(instance, "mDraPaiId")),
            uraDora = PaiId(FieldValue(instance, "mUraDraPaiId")),
            pais = Pais(instance)
        };
    }

    private static string? CurrentState(GameManager instance, string? mode)
    {
        return mode switch
        {
            "YakumanBonus" => FieldText(instance, "mGameStateBonus"),
            "ParenChallenge" => FieldText(instance, "mGameStateChallenge"),
            "Normal" => FieldText(instance, "mGameStateNormal"),
            _ => FieldText(instance, "mGameStateNormal")
        };
    }

    private static string? CurrentRequestState(GameManager instance, string? mode)
    {
        return mode switch
        {
            "YakumanBonus" => FieldText(instance, "mGameStateBonusRequest"),
            "ParenChallenge" => FieldText(instance, "mGameStateChallengeRequest"),
            "Normal" => FieldText(instance, "mGameStateNormalRequest"),
            _ => FieldText(instance, "mGameStateNormalRequest")
        };
    }

    private static List<int> Pais(GameManager instance)
    {
        var values = new List<int>();
        if (FieldValue(instance, "mPais") is not Array pais)
        {
            return values;
        }
        foreach (var pai in pais)
        {
            values.Add(PaiId(pai));
        }
        return values;
    }

    private static int PaiId(object? pai)
    {
        if (pai == null)
        {
            return BlankTile;
        }
        var value = FieldValue(pai, "mPaiId");
        if (value == null)
        {
            return BlankTile;
        }
        var text = value.ToString();
        if (text == null || text.Contains("BLANK"))
        {
            return BlankTile;
        }
        try
        {
            return Convert.ToInt32(value);
        }
        catch
        {
            return BlankTile;
        }
    }

    private static object? FieldValue(object instance, string name)
    {
        return AccessTools.Field(instance.GetType(), name)?.GetValue(instance);
    }

    private static string? FieldText(object instance, string name)
    {
        return FieldValue(instance, name)?.ToString();
    }

    private static int? FieldInt(object instance, string name)
    {
        var value = FieldValue(instance, name);
        if (value == null)
        {
            return null;
        }
        try
        {
            return Convert.ToInt32(value);
        }
        catch
        {
            return null;
        }
    }

    private static bool? FieldBool(object instance, string name)
    {
        return FieldValue(instance, name) as bool?;
    }
}

internal static class GameManagerSnapshotLog
{
    public static void Write(GameManager instance, string trigger)
    {
        ProbeLog.Write("game_state_snapshot", GameManagerProjection.Snapshot(instance, trigger));
    }
}

[HarmonyPatch(typeof(GameManager), "setModeState")]
internal static class GameManagerSetModeStatePatch
{
    private static void Postfix(GameManager __instance)
    {
        GameManagerSnapshotLog.Write(__instance, "set_mode_state");
    }
}

[HarmonyPatch(typeof(GameManager), nameof(GameManager.SetNormalRequest))]
internal static class GameManagerSetNormalRequestPatch
{
    private static void Postfix(GameManager __instance)
    {
        GameManagerSnapshotLog.Write(__instance, "set_normal_request");
    }
}

[HarmonyPatch(typeof(GameManager), nameof(GameManager.SetChallengeRequest))]
internal static class GameManagerSetChallengeRequestPatch
{
    private static void Postfix(GameManager __instance)
    {
        GameManagerSnapshotLog.Write(__instance, "set_challenge_request");
    }
}

[HarmonyPatch(typeof(GameManager), nameof(GameManager.SetBonusRequest))]
internal static class GameManagerSetBonusRequestPatch
{
    private static void Postfix(GameManager __instance)
    {
        GameManagerSnapshotLog.Write(__instance, "set_bonus_request");
    }
}

[HarmonyPatch(typeof(GameManager), nameof(GameManager.SetMainButton))]
internal static class GameManagerSetMainButtonPatch
{
    private static void Postfix(GameManager __instance)
    {
        GameManagerSnapshotLog.Write(__instance, "set_main_button");
    }
}

[HarmonyPatch(typeof(GameManager), nameof(GameManager.MainButtonClick))]
internal static class GameManagerMainButtonClickPatch
{
    private static void Postfix(GameManager __instance)
    {
        GameManagerSnapshotLog.Write(__instance, "main_button_click");
    }
}

[HarmonyPatch(typeof(GameManager), "Update")]
internal static class GameManagerUpdateBridgePatch
{
    private static bool ticking;

    private static void Postfix()
    {
        if (ticking)
        {
            return;
        }
        ticking = true;
        try
        {
            ActionBridge.Tick();
        }
        finally
        {
            ticking = false;
        }
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.RecvConfigOdds))]
internal static class RecvConfigOddsPatch
{
    private static void Postfix(JanQAPI __instance)
    {
        ProbeLog.Write("recv_config_odds", Projection.YakuOddsList(__instance.GetSpYakuOddsList())!);
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.RecvGameHaipai))]
internal static class RecvGameHaipaiPatch
{
    private static void Postfix(JanQAPI __instance)
    {
        ProbeLog.Write("recv_game_haipai", Projection.Haipai(__instance.GetSpGameHaipai())!);
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.RecvGameTsumo))]
internal static class RecvGameTsumoPatch
{
    private static void Postfix(JanQAPI __instance)
    {
        ProbeLog.Write("recv_game_tsumo", Projection.Tsumo(__instance.GetSpGameTsumo())!);
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.RecvActDahai))]
internal static class RecvActDahaiPatch
{
    private static void Postfix(JanQAPI __instance)
    {
        ProbeLog.Write("recv_act_dahai", Projection.Dahai(__instance.GetSpPlayerDahai())!);
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.RecvJanQResult))]
internal static class RecvJanQResultPatch
{
    private static void Postfix(JanQAPI __instance)
    {
        ProbeLog.Write("recv_janq_result", Projection.Result(__instance.GetSpJanQResult())!);
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.sendActionStart))]
internal static class SendActionStartPatch
{
    private static void Prefix()
    {
        ProbeLog.Write("send_action_start", new { });
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.sendActionShot))]
internal static class SendActionShotPatch
{
    private static void Prefix(int area)
    {
        ProbeLog.Write("send_action_shot", new { area });
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.sendActionDahai))]
internal static class SendActionDahaiPatch
{
    private static void Prefix(bool richi, int pos, int pai)
    {
        ProbeLog.Write("send_action_dahai", new { richi, pos, pai });
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.sendActionAgari))]
internal static class SendActionAgariPatch
{
    private static void Prefix(int yakuman, int han, int nb_omo, int nb_ura, List<int> tehai, List<int> yaku)
    {
        ProbeLog.Write("send_action_agari", new
        {
            yakuman,
            han,
            nbOmoDora = nb_omo,
            nbUraDora = nb_ura,
            tehai,
            yaku
        });
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.sendRyukyoku))]
internal static class SendRyukyokuPatch
{
    private static void Prefix()
    {
        ProbeLog.Write("send_ryukyoku", new { });
    }
}

[HarmonyPatch(typeof(JanQAPI), nameof(JanQAPI.sendGiveUp))]
internal static class SendGiveUpPatch
{
    private static void Prefix()
    {
        ProbeLog.Write("send_give_up", new { });
    }
}
