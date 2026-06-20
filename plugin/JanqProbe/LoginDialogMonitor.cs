using System;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace JanqProbe;

internal static class LoginDialogMonitor
{
    private static float nextCheckAt;
    private static string? lastBlockedSequence;

    public static void Tick()
    {
        if (JanqNavigator.Enabled || Time.realtimeSinceStartup < nextCheckAt)
        {
            return;
        }
        nextCheckAt = Time.realtimeSinceStartup + 0.5f;

        var sequence = CurrentLoginSequence();
        var sequenceName = sequence?.GetType().FullName;
        if (sequenceName != "Login.LoginErrorSequence")
        {
            lastBlockedSequence = null;
            return;
        }

        if (lastBlockedSequence == sequenceName)
        {
            return;
        }

        lastBlockedSequence = sequenceName;
        ProbeLog.Write("janq_runtime_login_dialog_observed", new
        {
            sequence = sequenceName,
            dialogReason = "account_conflict_or_login_error"
        });
        ProbeLog.Write("janq_runtime_login_blocked", new
        {
            sequence = sequenceName,
            reason = "account_conflict_or_login_error",
            dialogReason = "account_conflict_or_login_error"
        });
    }

    private static object? CurrentLoginSequence()
    {
        var loginManager = UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .FirstOrDefault(item => item.GetType().FullName == "LoginSequenceManager");
        if (loginManager == null)
        {
            return null;
        }

        object? sequence = AccessTools.Field(loginManager.GetType(), "_currentSequence")
            ?.GetValue(loginManager);
        while (sequence != null)
        {
            var inner = FieldInHierarchy(sequence.GetType(), "sequence")?.GetValue(sequence);
            if (inner == null || ReferenceEquals(inner, sequence))
            {
                return sequence;
            }
            sequence = inner;
        }
        return null;
    }

    private static FieldInfo? FieldInHierarchy(Type type, string name)
    {
        for (var current = type; current != null; current = current.BaseType)
        {
            var field = current.GetField(name, BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
            if (field != null)
            {
                return field;
            }
        }
        return null;
    }
}
