using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MJM.Sequence;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace JanqProbe;

internal static class JanqNavigator
{
    private static bool enabled;
    private static bool janqDestinationRequested;
    private static bool minimumBetSelected;
    private static float nextMenuJumpAt;
    private static float nextStatusLogAt;
    private static float nextScreenshotAt;

    public static bool Enabled => enabled;

    public static void Start()
    {
        enabled = true;
        janqDestinationRequested = false;
        minimumBetSelected = false;
        nextMenuJumpAt = 0f;
        nextStatusLogAt = 0f;
        nextScreenshotAt = 0f;
        ProbeLog.Write("janq_navigation_started", new { });
    }

    public static void Tick()
    {
        if (!enabled || UnityEngine.Time.realtimeSinceStartup < nextMenuJumpAt)
        {
            return;
        }

        if (UnityEngine.Object.FindObjectOfType<Janq.GameManager>() != null)
        {
            ProbeLog.Write("janq_navigation_ready", new { minimumBetSelected });
            enabled = false;
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

    public static void SelectMinimumBet(object betMenu, object config, object sessionData)
    {
        if (!enabled || minimumBetSelected)
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

        var selectedIndex = -1;
        var selectedBet = int.MaxValue;
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
            if (bet.Value < selectedBet)
            {
                selectedIndex = index;
                selectedBet = bet.Value;
            }
        }

        if (selectedIndex < 0)
        {
            ProbeLog.Write("janq_navigation_failed", new { stage = "minimum_bet_unavailable", level });
            return;
        }

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
            args: new object[] { selectedIndex, selectedBet },
            culture: null
        );
        field.SetValue(betMenu, request);
        minimumBetSelected = true;
        ProbeLog.Write("janq_navigation_minimum_bet_selected", new
        {
            index = selectedIndex,
            bet = selectedBet,
            level
        });
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
        JanqNavigator.SelectMinimumBet(__instance, in_config, in_sessionData);
    }
}
