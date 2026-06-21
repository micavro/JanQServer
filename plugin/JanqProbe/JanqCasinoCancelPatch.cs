using System;
using System.Collections.Generic;
using System.Net;
using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using UnityEngine;

namespace JanqProbe;

[HarmonyPatch]
internal static class JanqCasinoCancelPatch
{
    private const float PrimeDelaySeconds = 3.0f;
    private const float CancelFallbackTimeoutSeconds = 15.0f;
    private static readonly Dictionary<int, float> RecvFinishWaitStartedAt = new();
    private static readonly Dictionary<int, float> CancelFallbackStartedAt = new();
    private static readonly HashSet<int> CancelFallbackAttempted = new();

    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("Casino.CasinoPlayCancelSequence")
            ?? throw new MissingMemberException("Casino.CasinoPlayCancelSequence");
        return AccessTools.Method(type, "OnUpdate")
            ?? throw new MissingMethodException(type.FullName, "OnUpdate");
    }

    private static void Postfix(object __instance)
    {
        TryPrimeJanqFinishGameAfterDelay(__instance);
    }

    private static void TryPrimeJanqFinishGameAfterDelay(object sequence)
    {
        try
        {
            var casino = CurrentCasinoApi();
            if (casino == null || casino.GetType().FullName != "SphingoAPI.JanQAPI")
            {
                return;
            }

            var key = RuntimeHelpers.GetHashCode(sequence);
            var status = FieldInHierarchy(sequence.GetType(), "status")?.GetValue(sequence)?.ToString();
            if (status != "RECV_FINISH_GAME")
            {
                RecvFinishWaitStartedAt.Remove(key);
                return;
            }

            var getFinish = AccessTools.Method(casino.GetType(), "GetSpFinishGame");
            if (getFinish?.Invoke(casino, null) != null)
            {
                RecvFinishWaitStartedAt.Remove(key);
                ProbeLog.Write("janq_finish_game_already_available", new
                {
                    reason = "casino_play_cancel_recv_finish_wait",
                    status
                });
                return;
            }

            if (!RecvFinishWaitStartedAt.TryGetValue(key, out var startedAt))
            {
                startedAt = Time.realtimeSinceStartup;
                RecvFinishWaitStartedAt[key] = startedAt;
                ProbeLog.Write("janq_finish_game_wait_started", new
                {
                    reason = "casino_play_cancel_recv_finish_wait",
                    status,
                    delaySeconds = PrimeDelaySeconds
                });
                return;
            }

            var elapsed = Time.realtimeSinceStartup - startedAt;
            if (elapsed < PrimeDelaySeconds)
            {
                return;
            }

            var recvFinish = AccessTools.Method(casino.GetType(), "RecvFinishGame");
            if (recvFinish == null)
            {
                ProbeLog.Write("janq_finish_game_prime_failed", new
                {
                    reason = "casino_play_cancel_recv_finish_wait",
                    status,
                    elapsedSeconds = elapsed,
                    error = "RecvFinishGame_missing"
                });
                return;
            }

            recvFinish.Invoke(casino, new object[] { Array.Empty<int>(), 0 });
            RecvFinishWaitStartedAt.Remove(key);
            ProbeLog.Write("janq_finish_game_primed", new
            {
                reason = "casino_play_cancel_recv_finish_wait",
                status,
                elapsedSeconds = elapsed
            });
        }
        catch (Exception ex)
        {
            ProbeLog.Write("janq_finish_game_prime_failed", new
            {
                reason = "casino_play_cancel_recv_finish_wait",
                error = ex.ToString()
            });
        }
    }

    internal static bool TryStartCancelFallbackAfterFailedEnd(object sequence)
    {
        try
        {
            var casino = CurrentCasinoApi();
            if (casino == null || casino.GetType().FullName != "SphingoAPI.JanQAPI")
            {
                return false;
            }

            var status = FieldInHierarchy(sequence.GetType(), "status")?.GetValue(sequence)?.ToString();
            if (status != "WAIT_PLAY_END_RES")
            {
                return false;
            }

            var isFinish = FieldInHierarchy(sequence.GetType(), "isFinish")?.GetValue(sequence) as bool?;
            var isEndOK = FieldInHierarchy(sequence.GetType(), "isEndOK")?.GetValue(sequence) as bool?;
            if (isFinish != true || isEndOK == true)
            {
                return false;
            }

            var key = RuntimeHelpers.GetHashCode(sequence);
            if (CancelFallbackStartedAt.TryGetValue(key, out var startedAt))
            {
                if (Time.realtimeSinceStartup - startedAt > CancelFallbackTimeoutSeconds)
                {
                    CancelFallbackStartedAt.Remove(key);
                    SetField(sequence, "isFinish", true);
                    SetField(sequence, "isEndOK", false);
                    ProbeLog.Write("janq_cancel_fallback_timeout", new
                    {
                        status,
                        elapsedSeconds = Time.realtimeSinceStartup - startedAt
                    });
                    return false;
                }
                return true;
            }
            if (CancelFallbackAttempted.Contains(key))
            {
                return false;
            }

            SetField(sequence, "isFinish", false);
            CancelFallbackAttempted.Add(key);
            CancelFallbackStartedAt[key] = Time.realtimeSinceStartup;
            ProbeLog.Write("janq_cancel_fallback_started", new
            {
                status,
                reason = "casino_play_end_not_ok"
            });

            var appServerApi = AccessTools.TypeByName("MJM.Network.ApplicationServerAPI");
            var requestCancel = appServerApi == null
                ? null
                : AccessTools.Method(appServerApi, "RequestCasinoPlayCancel");
            var globalSceneManager = AccessTools.TypeByName("GlobalSceneManager");
            var getGameId = globalSceneManager == null
                ? null
                : AccessTools.Method(globalSceneManager, "GetCasinoGameID");
            if (requestCancel == null || getGameId == null)
            {
                throw new MissingMethodException("RequestCasinoPlayCancel/GetCasinoGameID");
            }

            var callbackType = requestCancel.GetParameters()[1].ParameterType;
            var callbackTarget = new CancelFallbackCallback(sequence, key);
            var callbackMethod = typeof(CancelFallbackCallback).GetMethod(
                nameof(CancelFallbackCallback.OnCancel),
                BindingFlags.Instance | BindingFlags.Public
            );
            var callback = Delegate.CreateDelegate(callbackType, callbackTarget, callbackMethod);
            requestCancel.Invoke(null, new[] { getGameId.Invoke(null, null), callback });
            return true;
        }
        catch (Exception ex)
        {
            CancelFallbackStartedAt.Remove(RuntimeHelpers.GetHashCode(sequence));
            SetField(sequence, "isFinish", true);
            SetField(sequence, "isEndOK", false);
            ProbeLog.Write("janq_cancel_fallback_failed", new
            {
                error = ex.ToString()
            });
            return false;
        }
    }

    private sealed class CancelFallbackCallback
    {
        private readonly object sequence;
        private readonly int key;

        public CancelFallbackCallback(object sequence, int key)
        {
            this.sequence = sequence;
            this.key = key;
        }

        public void OnCancel(bool success, HttpStatusCode statusCode, bool cancelOK)
        {
            CancelFallbackStartedAt.Remove(key);
            SetField(sequence, "isFinish", true);
            SetField(sequence, "isEndOK", success);
            ProbeLog.Write("janq_cancel_fallback_completed", new
            {
                success,
                statusCode = statusCode.ToString(),
                cancelOK,
                softEndOK = success
            });
        }
    }

    private static object? CurrentCasinoApi()
    {
        var globalSceneManager = AccessTools.TypeByName("GlobalSceneManager");
        var manager = globalSceneManager == null
            ? null
            : AccessTools.Field(globalSceneManager, "casinoGameManager")?.GetValue(null);
        if (manager == null)
        {
            return null;
        }

        return FieldInHierarchy(manager.GetType(), "casino")?.GetValue(manager);
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

    private static void SetField(object instance, string name, object? value)
    {
        FieldInHierarchy(instance.GetType(), name)?.SetValue(instance, value);
    }
}

[HarmonyPatch]
internal static class JanqCasinoCancelIsFinishPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("Casino.CasinoPlayCancelSequence")
            ?? throw new MissingMemberException("Casino.CasinoPlayCancelSequence");
        return AccessTools.Method(type, "IsFinish")
            ?? throw new MissingMethodException(type.FullName, "IsFinish");
    }

    private static void Postfix(object __instance, ref bool __result)
    {
        if (!__result)
        {
            return;
        }
        if (JanqCasinoCancelPatch.TryStartCancelFallbackAfterFailedEnd(__instance))
        {
            __result = false;
        }
    }
}
