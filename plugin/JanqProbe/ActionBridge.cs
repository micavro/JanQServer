using System;
using System.Collections;
using System.IO;
using System.Linq;
using HarmonyLib;
using Janq;
using Janq.Mahjong;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.UI;

namespace JanqProbe;

internal sealed class BridgeCommand
{
    public string? id { get; set; }

    public string? kind { get; set; }

    public string? createdAt { get; set; }

    public int? area { get; set; }

    public int? discardIndex { get; set; }

    public bool richi { get; set; }

    public string? account { get; set; }

    public string? accountSelector { get; set; }

    public string? accountRequestId { get; set; }

    public string? accountStorePath { get; set; }
}

internal sealed class ActiveBridgeCommand
{
    public ActiveBridgeCommand(BridgeCommand command, string workingPath)
    {
        Command = command;
        WorkingPath = workingPath;
        StartedAt = DateTimeOffset.UtcNow;
    }

    public BridgeCommand Command { get; }

    public string WorkingPath { get; }

    public DateTimeOffset StartedAt { get; }

    public int Stage { get; set; }

    public object? Context { get; set; }

    public string? LastStatusKey { get; set; }
}

internal static class ActionBridge
{
    private static readonly object Sync = new();
    private static readonly float[] ShotFillAmounts = { 0.25f, 0.35f, 0.435f, 0.50f, 0.565f, 0.65f, 0.80f };
    private static string commandsPath = "";
    private static string resultsPath = "";
    private static ActiveBridgeCommand? active;
    private static float nextPollAt;

    public static string RootPath { get; private set; } = "";

    public static void Initialize(string workspace)
    {
        workspace = Path.GetFullPath(workspace);
        RootPath = Path.Combine(workspace, "_runtime", "bridge");
        commandsPath = Path.Combine(RootPath, "commands");
        resultsPath = Path.Combine(RootPath, "results");
        Directory.CreateDirectory(commandsPath);
        Directory.CreateDirectory(resultsPath);
        RecoverWorkingFiles();
        ProbeLog.Write("bridge_ready", new { rootPath = RootPath });
    }

    public static void Shutdown()
    {
        lock (Sync)
        {
            if (active != null)
            {
                Finish(active, success: false, "plugin_shutdown", null);
                active = null;
            }
        }
    }

    public static void Tick()
    {
        if (string.IsNullOrEmpty(RootPath))
        {
            return;
        }

        lock (Sync)
        {
            if (active == null)
            {
                if (Time.realtimeSinceStartup < nextPollAt)
                {
                    return;
                }
                nextPollAt = Time.realtimeSinceStartup + 0.10f;
                active = ClaimNextCommand();
            }

            if (active != null)
            {
                Process(active);
            }
        }
    }

    private static ActiveBridgeCommand? ClaimNextCommand()
    {
        foreach (var path in Directory.GetFiles(commandsPath, "*.json").OrderBy(File.GetCreationTimeUtc))
        {
            var workingPath = path + ".working";
            try
            {
                File.Move(path, workingPath);
                var command = JsonConvert.DeserializeObject<BridgeCommand>(
                    File.ReadAllText(workingPath)
                );
                if (command == null || string.IsNullOrWhiteSpace(command.id) || string.IsNullOrWhiteSpace(command.kind))
                {
                    var invalid = new ActiveBridgeCommand(
                        command ?? new BridgeCommand { id = Path.GetFileNameWithoutExtension(path), kind = "invalid" },
                        workingPath
                    );
                    Finish(invalid, success: false, "invalid_command", null);
                    continue;
                }
                if (IsStale(command))
                {
                    var stale = new ActiveBridgeCommand(command, workingPath);
                    Finish(stale, success: false, "stale_command", null);
                    continue;
                }
                ProbeLog.Write("bridge_command_received", command);
                return new ActiveBridgeCommand(command, workingPath);
            }
            catch (IOException)
            {
                continue;
            }
            catch (Exception ex)
            {
                ProbeLog.Write("bridge_command_claim_failed", new { path, error = ex.ToString() });
                TryDelete(workingPath);
            }
        }
        return null;
    }

    private static bool IsStale(BridgeCommand command)
    {
        if (!DateTimeOffset.TryParse(command.createdAt, out var created))
        {
            return false;
        }
        return DateTimeOffset.UtcNow - created > TimeSpan.FromSeconds(180);
    }

    private static void Process(ActiveBridgeCommand item)
    {
        if (DateTimeOffset.UtcNow - item.StartedAt > TimeoutFor(item))
        {
            Finish(item, success: false, "local_action_timeout", FindGameManager());
            active = null;
            return;
        }

        try
        {
            if (item.Command.kind == CasinoExitBridge.Kind)
            {
                if (CasinoExitBridge.Process(item, out var success, out var error))
                {
                    Finish(item, success, error, FindGameManager());
                    active = null;
                }
                return;
            }
            if (item.Command.kind == AccountLoginBridge.Kind)
            {
                if (AccountLoginBridge.Process(item, out var success, out var error))
                {
                    Finish(item, success, error, FindGameManager());
                    active = null;
                }
                return;
            }
            if (item.Command.kind == "enter_janq")
            {
                JanqNavigator.Start();
                Finish(item, success: true, null, FindGameManager());
                active = null;
                return;
            }
            if (item.Command.kind == "reselect_bet")
            {
                JanqNavigator.Start(reselectFromGame: true);
                Finish(item, success: true, null, FindGameManager());
                active = null;
                return;
            }

            var manager = FindGameManager();
            if (manager == null)
            {
                return;
            }

            switch (item.Command.kind)
            {
                case "press_main":
                    PressMain(manager, item, allowAgari: false);
                    break;
                case "agari":
                    PressMain(manager, item, allowAgari: true);
                    break;
                case "shot":
                    Shoot(manager, item);
                    break;
                case "discard":
                    Discard(manager, item);
                    break;
                default:
                    throw new InvalidOperationException($"unsupported_action:{item.Command.kind}");
            }
        }
        catch (Exception ex)
        {
            Finish(item, success: false, ex.Message, FindGameManager());
            active = null;
        }
    }

    private static TimeSpan TimeoutFor(ActiveBridgeCommand item)
    {
        return item.Command.kind == AccountLoginBridge.Kind || item.Command.kind == CasinoExitBridge.Kind
            ? TimeSpan.FromSeconds(180)
            : TimeSpan.FromSeconds(60);
    }

    private static void PressMain(GameManager manager, ActiveBridgeCommand item, bool allowAgari)
    {
        var state = CurrentState(manager);
        var button = FieldText(manager, "mMainButtonType");
        var valid = allowAgari ? button == "Agari" : button == "Bet" || button == "Free";
        if (!valid)
        {
            if (!allowAgari && !string.IsNullOrWhiteSpace(state) && state != "BetWait" && state != "FreeWait")
            {
                ProbeLog.Write("bridge_stale_main_completed", new
                {
                    state,
                    button,
                    snapshot = GameManagerProjection.Snapshot(manager, "stale_main")
                });
                Finish(item, success: true, null, manager);
                active = null;
                return;
            }
            if (allowAgari && (state == "Result" || state == "AgariRun" || state == "BetWait"))
            {
                ProbeLog.Write("bridge_stale_agari_completed", new
                {
                    state,
                    button,
                    snapshot = GameManagerProjection.Snapshot(manager, "stale_agari")
                });
                Finish(item, success: true, null, manager);
                active = null;
            }
            return;
        }
        manager.MainButtonClick();
        Finish(item, success: true, null, manager);
        active = null;
    }

    private static void Shoot(GameManager manager, ActiveBridgeCommand item)
    {
        var area = item.Command.area ?? 0;
        if (area < 1 || area > 7)
        {
            throw new InvalidOperationException($"invalid_shot_area:{area}");
        }
        var state = CurrentState(manager);
        var button = FieldText(manager, "mMainButtonType");
        if (state != "ShootWait" || button != "Shot")
        {
            if (state == "UserWait" || state == "BetWait" || state == "FreeWait" || state == "Result" || state == "AgariRun")
            {
                ProbeLog.Write("bridge_stale_shot_completed", new
                {
                    requestedArea = area,
                    state,
                    button,
                    snapshot = GameManagerProjection.Snapshot(manager, "stale_shot")
                });
                Finish(item, success: true, null, manager);
                active = null;
            }
            return;
        }
        var shotObject = AccessTools.Field(typeof(GameManager), "mShotObject")?.GetValue(manager) as Image;
        if (shotObject == null)
        {
            return;
        }
        shotObject.fillAmount = ShotFillAmounts[area - 1];
        manager.MainButtonClick();
        Finish(item, success: true, null, manager);
        active = null;
    }

    private static void Discard(GameManager manager, ActiveBridgeCommand item)
    {
        var oneBasedIndex = item.Command.discardIndex ?? 0;
        if (oneBasedIndex < 1 || oneBasedIndex > 14)
        {
            throw new InvalidOperationException($"invalid_discard_index:{oneBasedIndex}");
        }
        var state = CurrentState(manager);
        if (state != "UserWait")
        {
            return;
        }

        if (item.Command.richi && item.Stage == 0)
        {
            var reach = FieldText(manager, "mReachButtonType");
            if (reach != "ReachOff")
            {
                return;
            }
            manager.ReachButtonClick();
            item.Stage = 1;
            return;
        }

        if (item.Command.richi && item.Stage == 1)
        {
            var reach = FieldText(manager, "mReachButtonType");
            if (reach != "ReachOn")
            {
                return;
            }
            item.Stage = 2;
        }

        var paiButtonType = FieldText(manager, "mPaiButtonType");
        if (paiButtonType != "Sute")
        {
            return;
        }
        SelectDiscard(manager, oneBasedIndex - 1);
        Finish(item, success: true, null, manager);
        active = null;
    }

    private static void SelectDiscard(GameManager manager, int index)
    {
        var buttons = AccessTools.Field(typeof(GameManager), "mButtons")?.GetValue(manager) as IList;
        if (buttons == null || index >= buttons.Count || buttons[index] is not MyPaiButton button)
        {
            throw new InvalidOperationException($"discard_button_missing:{index + 1}");
        }
        var animator = button.mAnimater;
        manager.OnPaiEnter(animator, index);
        manager.OnPaiClick(animator, index);
        manager.OnPaiClick(animator, index);
    }

    private static GameManager? FindGameManager()
    {
        return UnityEngine.Object.FindObjectOfType<GameManager>();
    }

    private static string? CurrentState(GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonus"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallenge"),
            _ => FieldText(manager, "mGameStateNormal")
        };
    }

    private static string? FieldText(object instance, string name)
    {
        return AccessTools.Field(instance.GetType(), name)?.GetValue(instance)?.ToString();
    }

    private static void Finish(
        ActiveBridgeCommand item,
        bool success,
        string? error,
        GameManager? manager
    )
    {
        var result = new
        {
            id = item.Command.id,
            kind = item.Command.kind,
            success,
            error,
            completedAt = DateTimeOffset.UtcNow.ToString("O"),
            state = manager == null ? null : GameManagerProjection.Snapshot(manager, "bridge_result")
        };
        var resultPath = Path.Combine(resultsPath, item.Command.id + ".json");
        var tempPath = resultPath + ".tmp";
        File.WriteAllText(tempPath, JsonConvert.SerializeObject(result));
        if (File.Exists(resultPath))
        {
            File.Delete(resultPath);
        }
        File.Move(tempPath, resultPath);
        ProbeLog.Write(success ? "bridge_command_completed" : "bridge_command_failed", result);
        TryDelete(item.WorkingPath);
    }

    private static void RecoverWorkingFiles()
    {
        foreach (var path in Directory.GetFiles(commandsPath, "*.working"))
        {
            TryDelete(path);
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch
        {
        }
    }
}
