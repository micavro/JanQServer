using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using Newtonsoft.Json;
using MJM.Sequence;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace JanqProbe;

internal static class JanqNavigator
{
    private static bool enabled;
    private static bool janqDestinationRequested;
    private static bool betSelected;
    private static bool reselectFromGame;
    private static bool reselectExitRequested;
    private static int loginDialogDismissCount;
    private static float reselectStartedAt;
    private static float nextMenuJumpAt;
    private static float nextStatusLogAt;
    private static float nextScreenshotAt;

    public static bool Enabled => enabled;

    public static void Start(bool reselectFromGame = false)
    {
        enabled = true;
        janqDestinationRequested = false;
        betSelected = false;
        JanqNavigator.reselectFromGame = reselectFromGame;
        reselectExitRequested = false;
        loginDialogDismissCount = 0;
        reselectStartedAt = UnityEngine.Time.realtimeSinceStartup;
        nextMenuJumpAt = 0f;
        nextStatusLogAt = 0f;
        nextScreenshotAt = 0f;
        ProbeLog.Write("janq_navigation_started", new { reselectFromGame });
    }

    public static void Tick()
    {
        if (!enabled || UnityEngine.Time.realtimeSinceStartup < nextMenuJumpAt)
        {
            return;
        }

        var manager = UnityEngine.Object.FindObjectOfType<Janq.GameManager>();
        if (manager != null)
        {
            if (reselectFromGame && !betSelected)
            {
                if (reselectExitRequested && UnityEngine.Time.realtimeSinceStartup - reselectStartedAt > 12f)
                {
                    ProbeLog.Write("janq_navigation_reselect_failed", CurrentGameStatePayload(manager));
                    enabled = false;
                    reselectFromGame = false;
                    return;
                }
                if (!IsSafeReselectPoint(manager))
                {
                    ProbeLog.Write("janq_navigation_reselect_waiting", CurrentGameStatePayload(manager));
                    nextMenuJumpAt = UnityEngine.Time.realtimeSinceStartup + 1f;
                    return;
                }
                if (!reselectExitRequested)
                {
                    string reselectMethod;
                    try
                    {
                        reselectMethod = RequestEnterBetMenuFromGame(manager);
                    }
                    catch (Exception ex)
                    {
                        ProbeLog.Write("janq_navigation_reselect_failed", CurrentGameStatePayload(manager, error: ex.ToString()));
                        enabled = false;
                        reselectFromGame = false;
                        return;
                    }
                    reselectExitRequested = true;
                    ProbeLog.Write("janq_navigation_reselect_exit_sent", CurrentGameStatePayload(manager, reselectMethod));
                }
                else
                {
                    ProbeLog.Write("janq_navigation_reselect_waiting_exit", CurrentGameStatePayload(manager));
                }
                nextMenuJumpAt = UnityEngine.Time.realtimeSinceStartup + 1f;
                return;
            }
            ProbeLog.Write("janq_navigation_ready", new
            {
                betSelected,
                reselectFromGame,
                currentBet = FieldInt(manager, "mBets")
            });
            enabled = false;
            reselectFromGame = false;
            return;
        }

        AdvanceKnownStartupState();
        if (!janqDestinationRequested)
        {
            MenuSequenceManager.RequestMenuJumpSequence(MenuSequenceManager.MenuJumpSequence.CASINO);
        }
        if (Time.realtimeSinceStartup >= nextStatusLogAt)
        {
            WriteWaitingStatus();
            nextStatusLogAt = Time.realtimeSinceStartup + 5f;
        }
        nextMenuJumpAt = UnityEngine.Time.realtimeSinceStartup + 1f;
    }

    public static void UseJanqDestination(object menu, object result, object modeInfo)
    {
        if (!enabled || janqDestinationRequested || FieldText(modeInfo, "category") != "JanQ")
        {
            return;
        }

        var destination = AccessTools.Property(result.GetType(), "Destination")?.GetValue(result)
            as SequenceBase<MenuSequenceManager>;
        var parent = AccessTools.Field(typeof(SequenceBase<MenuSequenceManager>), "parent")?.GetValue(menu)
            as MenuSequenceManager;
        if (destination == null || parent == null)
        {
            ProbeLog.Write("janq_navigation_failed", new { stage = "casino_destination_missing" });
            return;
        }

        if (parent.RequestForceNextSequence(destination))
        {
            janqDestinationRequested = true;
            ProbeLog.Write("janq_navigation_destination_requested", new { });
        }
    }

    public static void SelectConfiguredBet(object betMenu, object config, object sessionData)
    {
        if (!enabled || betSelected)
        {
            return;
        }

        var betTable = FieldValue(config, "betTable") as IList;
        var level = FieldInt(sessionData, "level");
        var adsBetUp = FieldBool(betMenu, "is_Ads_BetUP");
        if (betTable == null || level == null || adsBetUp == null)
        {
            ProbeLog.Write("janq_navigation_failed", new { stage = "bet_data_missing" });
            return;
        }

        var settings = BridgeSettings.Load();
        var candidates = new List<BetCandidate>();
        for (var index = 0; index < betTable.Count; index++)
        {
            var unit = betTable[index];
            if (unit == null)
            {
                continue;
            }
            var bet = FieldInt(unit, "bet");
            var borderLevel = FieldInt(unit, "borderLevel");
            var x2Mode = FieldInt(unit, "x2mode");
            if (bet == null || borderLevel == null || x2Mode == null)
            {
                continue;
            }
            if (x2Mode.Value != (adsBetUp.Value ? 1 : 0) || level.Value < borderLevel.Value)
            {
                continue;
            }
            candidates.Add(new BetCandidate(index, bet.Value, borderLevel.Value, x2Mode.Value));
        }

        if (candidates.Count == 0)
        {
            ProbeLog.Write("janq_navigation_failed", new { stage = "bet_unavailable", level, settings.targetBet });
            return;
        }
        var selection = SelectBetCandidate(candidates, settings.targetBet);

        var paramType = AccessTools.TypeByName("Casino.BetMenu+SendBetRateParam");
        var field = AccessTools.Field(betMenu.GetType(), "send_bet_rate");
        if (paramType == null || field == null)
        {
            ProbeLog.Write("janq_navigation_failed", new { stage = "bet_request_field_missing" });
            return;
        }

        var request = Activator.CreateInstance(
            paramType,
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
            binder: null,
            args: new object[] { selection.Candidate.Index, selection.Candidate.Bet },
            culture: null
        );
        field.SetValue(betMenu, request);
        betSelected = true;
        ProbeLog.Write("janq_navigation_bet_selected", new
        {
            index = selection.Candidate.Index,
            bet = selection.Candidate.Bet,
            targetBet = settings.targetBet,
            selectionMode = selection.Mode,
            level,
            policyReason = settings.policyReason,
            candidates
        });
    }

    private static BetSelection SelectBetCandidate(List<BetCandidate> candidates, int? targetBet)
    {
        var ordered = candidates.OrderBy(item => item.Bet).ToList();
        if (targetBet == null)
        {
            return new BetSelection(ordered[0], "minimum");
        }

        var exact = ordered.FirstOrDefault(item => item.Bet == targetBet.Value);
        if (exact != null)
        {
            return new BetSelection(exact, "exact");
        }

        var below = ordered.LastOrDefault(item => item.Bet <= targetBet.Value);
        if (below != null)
        {
            return new BetSelection(below, "highest_below_target");
        }

        return new BetSelection(ordered[0], "minimum_above_target");
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
        try
        {
            var value = FieldValue(instance, name);
            return value == null ? null : Convert.ToInt32(value);
        }
        catch
        {
            return null;
        }
    }

    private static bool? FieldBool(object instance, string name)
    {
        var value = FieldValue(instance, name);
        return value is bool flag ? flag : null;
    }

    private static bool IsSafeReselectPoint(Janq.GameManager manager)
    {
        var state = CurrentGameState(manager);
        var requestState = CurrentGameRequestState(manager);
        var button = FieldText(manager, "mMainButtonType");
        var buttonRequest = FieldText(manager, "mMainButtonRequest");
        var exitButton = FieldText(manager, "mExitButtonType");
        var exitButtonRequest = FieldText(manager, "mExitButtonRequest");
        return (state == "BetWait" || requestState == "BetWait")
            && (button == "Bet" || buttonRequest == "Bet")
            && (exitButton == "Exit" || exitButtonRequest == "Exit");
    }

    private static object CurrentGameStatePayload(
        Janq.GameManager manager,
        string? reselectMethod = null,
        string? error = null)
    {
        return new
        {
            gameMode = FieldText(manager, "mGameMode"),
            state = CurrentGameState(manager),
            requestState = CurrentGameRequestState(manager),
            mainButtonType = FieldText(manager, "mMainButtonType"),
            mainButtonRequest = FieldText(manager, "mMainButtonRequest"),
            exitButtonType = FieldText(manager, "mExitButtonType"),
            exitButtonRequest = FieldText(manager, "mExitButtonRequest"),
            currentBet = FieldInt(manager, "mBets"),
            casinoState = CasinoManagerState(),
            reselectMethod,
            error
        };
    }

    private static string RequestEnterBetMenuFromGame(Janq.GameManager manager)
    {
        manager.ExitButtonClick();
        return "janq_exit_button_listener";
    }

    private static string RequestEnterBetMenuFromCasinoState()
    {
        var globalSceneManager = AccessTools.TypeByName("GlobalSceneManager")
            ?? throw new InvalidOperationException("GlobalSceneManager_missing");
        var getJanqApi = AccessTools.Method(globalSceneManager, "GetJanQAPI")
            ?? throw new InvalidOperationException("GetJanQAPI_missing");
        var api = getJanqApi.Invoke(null, null)
            ?? throw new InvalidOperationException("JanQAPI_missing");
        var sendSelectBetRate = AccessTools.Method(api.GetType(), "sendSelectBetRate")
            ?? throw new InvalidOperationException("sendSelectBetRate_missing");
        sendSelectBetRate.Invoke(api, new object[] { -1, 0 });
        return "casino_state_send_select_bet_rate_exit";
    }

    private static string? CasinoManagerState()
    {
        var globalSceneManager = AccessTools.TypeByName("GlobalSceneManager");
        var manager = globalSceneManager == null
            ? null
            : AccessTools.Field(globalSceneManager, "casinoGameManager")?.GetValue(null);
        var state = manager == null
            ? null
            : AccessTools.Field(manager.GetType(), "state")?.GetValue(manager);
        return state?.GetType().FullName;
    }

    private static string? CurrentGameState(Janq.GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonus"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallenge"),
            _ => FieldText(manager, "mGameStateNormal")
        };
    }

    private static string? CurrentGameRequestState(Janq.GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonusRequest"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallengeRequest"),
            _ => FieldText(manager, "mGameStateNormalRequest")
        };
    }

    private static void WriteWaitingStatus()
    {
        var managers = UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .Where(item => item.GetType().FullName?.Contains("SequenceManager") == true)
            .Select(item => new
            {
                manager = item.GetType().FullName,
                sequence = CurrentInnerSequence(item)?.GetType().FullName
            })
            .OrderBy(item => item.manager)
            .ToArray();
        var regularTransition = AccessTools.Property(typeof(MenuSequenceManager), "IsRegularTransitionActive")
            ?.GetValue(null);
        ProbeLog.Write("janq_navigation_waiting", new
        {
            scene = SceneManager.GetActiveScene().name,
            managers,
            regularTransition
        });
        CaptureScreenshot(SceneManager.GetActiveScene().name);
    }

    private static void CaptureScreenshot(string scene)
    {
        if (Time.realtimeSinceStartup < nextScreenshotAt)
        {
            return;
        }
        nextScreenshotAt = Time.realtimeSinceStartup + 10f;
        var directory = Path.GetFullPath(Path.Combine(ActionBridge.RootPath, "..", "screenshots"));
        Directory.CreateDirectory(directory);
        var path = Path.Combine(
            directory,
            $"{DateTimeOffset.UtcNow:yyyyMMdd_HHmmss}_{scene}.png"
        );
        ScreenCapture.CaptureScreenshot(path);
        ProbeLog.Write("runtime_screenshot_requested", new { scene, path });
    }

    private static void AdvanceKnownStartupState()
    {
        var loginManager = UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .FirstOrDefault(item => item.GetType().FullName == "LoginSequenceManager");
        var sequence = CurrentInnerSequence(loginManager);
        var sequenceName = sequence?.GetType().FullName;
        if (TryAdvanceKnownLoginDialog(sequence, sequenceName))
        {
            return;
        }
        if (sequenceName == "Login.SegaLogoSequence")
        {
            var onTouch = AccessTools.Method(sequence!.GetType(), "onTouch");
            if (onTouch == null)
            {
                return;
            }
            onTouch.Invoke(sequence, new object?[] { null, null });
            ProbeLog.Write("janq_navigation_startup_advanced", new { sequence = sequenceName });
            return;
        }

        if (sequenceName != "Login.LoginButtonSequence")
        {
            return;
        }

        var button = AccessTools.Field(sequence!.GetType(), "login_button")?.GetValue(sequence);
        var onPushAnimEnded = button == null
            ? null
            : AccessTools.Field(button.GetType(), "OnPushAnimEnded")?.GetValue(button);
        var exec = onPushAnimEnded == null
            ? null
            : AccessTools.Method(onPushAnimEnded.GetType(), "Exec");
        if (exec == null)
        {
            return;
        }
        exec.Invoke(onPushAnimEnded, new[] { sequence });
        ProbeLog.Write("janq_navigation_startup_advanced", new { sequence = sequenceName });
    }

    private static bool TryAdvanceKnownLoginDialog(object? sequence, string? sequenceName)
    {
        if (sequence == null || string.IsNullOrWhiteSpace(sequenceName))
        {
            return false;
        }
        var currentSequenceName = sequenceName!;

        var isSafetyLogout = currentSequenceName.Contains("SafetyLogoutDialogSequence");
        var isMessageDialog = currentSequenceName.Contains("MessageDialogSequence");
        var isLoginError = currentSequenceName == "Login.LoginErrorSequence";
        if (!isSafetyLogout && !isMessageDialog && !isLoginError)
        {
            return false;
        }
        var dialogReason = isLoginError
            ? "account_conflict_or_login_error"
            : "startup_logout_or_message_dialog";

        if (loginDialogDismissCount >= 3)
        {
            ProbeLog.Write("janq_navigation_login_blocked", new
            {
                sequence = currentSequenceName,
                dismissCount = loginDialogDismissCount,
                reason = "repeated_login_dialog",
                dialogReason
            });
            enabled = false;
            return true;
        }

        try
        {
            if (isLoginError)
            {
                var prev = AccessTools.Field(sequence.GetType(), "prev")?.GetValue(sequence);
                var nextField = AccessTools.Field(sequence.GetType(), "next");
                if (prev == null || nextField == null)
                {
                    return false;
                }
                nextField.SetValue(sequence, prev);
            }
            else
            {
                var isFinishField = AccessTools.Field(sequence.GetType(), "isFinish");
                if (isFinishField == null)
                {
                    return false;
                }
                isFinishField.SetValue(sequence, true);
            }

            loginDialogDismissCount += 1;
            ProbeLog.Write("janq_navigation_login_dialog_dismissed", new
            {
                sequence = currentSequenceName,
                dismissCount = loginDialogDismissCount,
                dialogReason
            });
            return true;
        }
        catch (Exception ex)
        {
            ProbeLog.Write("janq_navigation_login_dialog_failed", new
            {
                sequence = currentSequenceName,
                error = ex.ToString()
            });
            return false;
        }
    }

    private static object? CurrentInnerSequence(object? manager)
    {
        if (manager == null)
        {
            return null;
        }
        object? sequence = AccessTools.Field(manager.GetType(), "_currentSequence")?.GetValue(manager);
        while (sequence != null)
        {
            var inner = AccessTools.Field(sequence.GetType(), "sequence")?.GetValue(sequence);
            if (inner == null || ReferenceEquals(inner, sequence))
            {
                return sequence;
            }
            sequence = inner;
        }
        return null;
    }
}

internal sealed class BridgeSettings
{
    public int? targetBet { get; set; }

    public string? policyReason { get; set; }

    public static BridgeSettings Load()
    {
        try
        {
            var path = Path.Combine(ActionBridge.RootPath, "settings.json");
            if (!File.Exists(path))
            {
                return new BridgeSettings();
            }
            return JsonConvert.DeserializeObject<BridgeSettings>(File.ReadAllText(path))
                ?? new BridgeSettings();
        }
        catch (Exception ex)
        {
            ProbeLog.Write("janq_navigation_settings_failed", new { error = ex.ToString() });
            return new BridgeSettings();
        }
    }
}

internal sealed class BetCandidate
{
    public BetCandidate(int index, int bet, int borderLevel, int x2Mode)
    {
        Index = index;
        Bet = bet;
        BorderLevel = borderLevel;
        X2Mode = x2Mode;
    }

    public int Index { get; }

    public int Bet { get; }

    public int BorderLevel { get; }

    public int X2Mode { get; }
}

internal sealed class BetSelection
{
    public BetSelection(BetCandidate candidate, string mode)
    {
        Candidate = candidate;
        Mode = mode;
    }

    public BetCandidate Candidate { get; }

    public string Mode { get; }
}

[HarmonyPatch]
internal static class CasinoJanqDestinationPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("Menu.SubMenu_Casino")
            ?? throw new MissingMemberException("Menu.SubMenu_Casino");
        return AccessTools.Method(type, "CreateButton")
            ?? throw new MissingMethodException(type.FullName, "CreateButton");
    }

    private static void Postfix(object __instance, object __result, object info)
    {
        JanqNavigator.UseJanqDestination(__instance, __result, info);
    }
}

[HarmonyPatch]
internal static class CasinoMinimumBetPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("Casino.BetMenu")
            ?? throw new MissingMemberException("Casino.BetMenu");
        return AccessTools.Method(type, "createBetButtonAll")
            ?? throw new MissingMethodException(type.FullName, "createBetButtonAll");
    }

    private static void Postfix(object __instance, object in_config, object in_sessionData)
    {
        JanqNavigator.SelectConfiguredBet(__instance, in_config, in_sessionData);
    }
}
