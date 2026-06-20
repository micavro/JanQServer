using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace JanqProbe;

internal static class CasinoExitBridge
{
    public const string Kind = "exit_to_login";

    public static bool Process(
        ActiveBridgeCommand item,
        out bool success,
        out string? error
    )
    {
        success = false;
        error = null;

        var scene = SceneManager.GetActiveScene().name;
        if (TryConfirmDialogButton("OK", "exit_to_login_dialog_confirmed")
            || TryConfirmDialogButton("BackToTitle", "exit_to_login_dialog_confirmed")
            || TryConfirmDialogButton("GotoTitle", "exit_to_login_dialog_confirmed")
            || TryFinishKnownDialogSequence(item, scene))
        {
            return false;
        }

        if (scene == "Login")
        {
            success = true;
            ProbeLog.Write("exit_to_login_completed", new { scene, item.Stage });
            return true;
        }

        if (scene == "Regist")
        {
            SceneManager.LoadScene("Login");
            item.Stage = Math.Max(item.Stage, 3);
            WriteStage("registration_scene_returning_to_login", item, scene, null);
            return false;
        }

        var gameManager = UnityEngine.Object.FindObjectOfType<Janq.GameManager>();
        if (gameManager != null)
        {
            if (item.Stage < 1 && !IsSafeJanqExitPoint(gameManager))
            {
                WriteStage("waiting_safe_janq_exit", item, scene, GameStatePayload(gameManager));
                return false;
            }

            if (item.Stage < 1)
            {
                gameManager.ExitButtonClick();
                item.Stage = 1;
                WriteStage("janq_exit_to_bet_menu_requested", item, scene, new
                {
                    state = GameStatePayload(gameManager),
                    casinoSequence = CurrentInnerSequence(FindManager("Casino.CasinoSequenceManager"))?.GetType().FullName,
                    loadedScenes = LoadedSceneNames()
                });
                return false;
            }

            WriteStage("waiting_janq_exit_to_bet_menu", item, scene, new
            {
                state = GameStatePayload(gameManager),
                casinoSequence = CurrentInnerSequence(FindManager("Casino.CasinoSequenceManager"))?.GetType().FullName,
                loadedScenes = LoadedSceneNames()
            });
            return false;
        }

        if (scene == "JanqGame" || scene == "BlackJack")
        {
            var casinoManager = FindManager("Casino.CasinoSequenceManager");
            if (casinoManager == null)
            {
                WriteStage("waiting_casino_manager", item, scene, null);
                return false;
            }
            if (!TryRequestBetMenuBack(casinoManager, item, scene))
            {
                WriteStage("waiting_casino_exit_sequence", item, scene, new
                {
                    casinoSequence = CurrentInnerSequence(casinoManager)?.GetType().FullName,
                    loadedScenes = LoadedSceneNames()
                });
            }
            return false;
        }

        if (scene == "Menu")
        {
            var menuManager = FindManager("MenuSequenceManager");
            if (menuManager == null)
            {
                WriteStage("waiting_menu_manager", item, scene, null);
                return false;
            }
            if (item.Stage < 3)
            {
                var logout = CreateInstance("Menu.LogoutSequence", menuManager);
                if (ForceNext(menuManager, logout))
                {
                    item.Stage = 3;
                    WriteStage("logout_requested", item, scene, CurrentInnerSequence(menuManager)?.GetType().FullName);
                }
                else
                {
                    WriteStage("waiting_logout_force_next", item, scene, CurrentInnerSequence(menuManager)?.GetType().FullName);
                }
            }
            else
            {
                WriteStage("waiting_logout_completion", item, scene, CurrentInnerSequence(menuManager)?.GetType().FullName);
            }
            return false;
        }

        WriteStage("waiting_supported_exit_scene", item, scene, null);
        return false;
    }

    private static bool TryRequestBetMenuBack(object casinoManager, ActiveBridgeCommand item, string scene)
    {
        var current = CurrentInnerSequence(casinoManager);
        var currentName = current?.GetType().FullName;
        if (currentName == "Casino.BetMenu")
        {
            if (item.Stage < 2)
            {
                if (!IsBetMenuReadyForBack(current!))
                {
                    WriteStage("waiting_bet_menu_ready_for_back", item, scene, BetMenuReadinessPayload(current!));
                    return true;
                }
                MenuSequenceManager.LastAutholizedGame = MenuSequenceManager.LastPlayAutholizedGameType.CASINO;
                MenuSequenceManager.DestinationFromCasinoScene = MenuSequenceManager.Destination.MAIN_MENU;
                SetField(current!, "isBack", true);
                item.Stage = 2;
                WriteStage("bet_menu_back_requested", item, scene, new
                {
                    casinoSequence = currentName,
                    loadedScenes = LoadedSceneNames()
                });
            }
            else
            {
                WriteStage("waiting_bet_menu_back_completion", item, scene, new
                {
                    casinoSequence = currentName,
                    loadedScenes = LoadedSceneNames()
                });
            }
            return true;
        }

        if (currentName == "Casino.CasinoPlayCancelSequence")
        {
            item.Stage = Math.Max(item.Stage, 2);
            WriteStage("waiting_casino_cancel_completion", item, scene, new
            {
                casinoSequence = currentName,
                loadedScenes = LoadedSceneNames()
            });
            return true;
        }

        if (currentName == "Casino.LeaveCasinoSceneSequence")
        {
            item.Stage = Math.Max(item.Stage, 2);
            WriteStage("waiting_leave_casino_scene", item, scene, new
            {
                casinoSequence = currentName,
                loadedScenes = LoadedSceneNames()
            });
            return true;
        }

        return false;
    }

    private static bool IsBetMenuReadyForBack(object betMenu)
    {
        return FieldValue(betMenu, "m_load_addon_task") == null
            && FieldValue(betMenu, "m_casino_stats_polling_task") == null
            && FieldValue(betMenu, "busyAuth") == null
            && FieldValue(betMenu, "backButton") != null;
    }

    private static object BetMenuReadinessPayload(object betMenu)
    {
        return new
        {
            loadAddonTask = FieldValue(betMenu, "m_load_addon_task") != null,
            casinoStatsPollingTask = FieldValue(betMenu, "m_casino_stats_polling_task") != null,
            busyAuth = FieldValue(betMenu, "busyAuth") != null,
            backButton = FieldValue(betMenu, "backButton") != null,
            sendBetRate = FieldValue(betMenu, "send_bet_rate") != null,
            recvBetRate = FieldValue(betMenu, "recv_bet_rate") != null,
            loadedScenes = LoadedSceneNames()
        };
    }

    private static bool TryRequestCasinoCancel(object casinoManager, ActiveBridgeCommand item, string scene, string requestStage)
    {
        var current = CurrentInnerSequence(casinoManager);
        var currentName = current?.GetType().FullName;
        if (currentName == "Casino.CasinoPlayCancelSequence")
        {
            item.Stage = Math.Max(item.Stage, 1);
            WriteStage("waiting_casino_cancel_completion", item, scene, currentName);
            return true;
        }

        if (item.Stage >= 1)
        {
            WriteStage("waiting_casino_cancel_completion", item, scene, currentName);
            return true;
        }

        var cancel = CreateSequence("Casino.CasinoPlayCancelSequence", casinoManager);
        if (ForceNext(casinoManager, cancel))
        {
            item.Stage = Math.Max(item.Stage, 1);
            WriteStage(requestStage, item, scene, new
            {
                previousSequence = currentName,
                loadedScenes = LoadedSceneNames()
            });
        }
        else
        {
            WriteStage("waiting_casino_cancel_force_next", item, scene, currentName);
        }
        return true;
    }

    private static bool IsSafeJanqExitPoint(Janq.GameManager manager)
    {
        var state = CurrentGameState(manager);
        var requestState = CurrentGameRequestState(manager);
        var exit = FieldText(manager, "mExitButtonType");
        var exitRequest = FieldText(manager, "mExitButtonRequest");
        return (state == "BetWait" || requestState == "BetWait")
            && (exit == "Exit" || exitRequest == "Exit");
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

    private static object GameStatePayload(Janq.GameManager manager)
    {
        return new
        {
            gameMode = FieldText(manager, "mGameMode"),
            state = CurrentGameState(manager),
            requestState = CurrentGameRequestState(manager),
            mainButtonType = FieldText(manager, "mMainButtonType"),
            mainButtonRequest = FieldText(manager, "mMainButtonRequest"),
            exitButtonType = FieldText(manager, "mExitButtonType"),
            exitButtonRequest = FieldText(manager, "mExitButtonRequest")
        };
    }

    private static string? FieldText(object instance, string name)
    {
        return FieldValue(instance, name)?.ToString();
    }

    private static object? FieldValue(object instance, string name)
    {
        return FieldInHierarchy(instance.GetType(), name)?.GetValue(instance);
    }

    private static object? GetMember(object instance, string name)
    {
        var property = AccessTools.Property(instance.GetType(), name);
        if (property != null)
        {
            return property.GetValue(instance);
        }
        return FieldValue(instance, name);
    }

    private static void SetField(object instance, string name, object? value)
    {
        var field = FieldInHierarchy(instance.GetType(), name)
            ?? throw new MissingFieldException(instance.GetType().FullName, name);
        field.SetValue(instance, value);
    }

    private static object? FindManager(string fullName)
    {
        var requestedType = AccessTools.TypeByName(fullName);
        return UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .FirstOrDefault(item =>
                item.GetType().FullName == fullName
                || (requestedType != null && requestedType.IsAssignableFrom(item.GetType())));
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
            var inner = FieldInHierarchy(sequence.GetType(), "sequence")?.GetValue(sequence);
            if (inner == null || ReferenceEquals(inner, sequence))
            {
                return sequence;
            }
            sequence = inner;
        }
        return null;
    }

    private static object CreateSequence(string typeName, params object[] args)
    {
        var type = RequiredType(typeName);
        var method = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Where(candidate => candidate.Name == "Create")
            .FirstOrDefault(candidate => ParametersMatch(candidate.GetParameters(), args))
            ?? throw new MissingMethodException(typeName, "Create");
        return method.Invoke(null, args)
            ?? throw new InvalidOperationException(typeName + ".Create returned null");
    }

    private static object CreateInstance(string typeName, params object[] args)
    {
        return Activator.CreateInstance(
            RequiredType(typeName),
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
            binder: null,
            args,
            culture: null
        ) ?? throw new InvalidOperationException(typeName + " constructor returned null");
    }

    private static bool ForceNext(object manager, object next)
    {
        var method = manager.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .FirstOrDefault(candidate => candidate.Name == "RequestForceNextSequence" && candidate.GetParameters().Length == 1)
            ?? throw new MissingMethodException(manager.GetType().FullName, "RequestForceNextSequence");
        return method.Invoke(manager, new[] { next }) as bool? ?? false;
    }

    private static bool ParametersMatch(ParameterInfo[] parameters, object[] args)
    {
        if (parameters.Length != args.Length)
        {
            return false;
        }
        for (var index = 0; index < parameters.Length; index++)
        {
            if (args[index] != null && !parameters[index].ParameterType.IsInstanceOfType(args[index]))
            {
                return false;
            }
        }
        return true;
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

    private static Type RequiredType(string name)
    {
        return AccessTools.TypeByName(name) ?? throw new TypeLoadException(name);
    }

    private static bool TryConfirmDialogButton(string requestedButton, string eventType)
    {
        var dialogType = AccessTools.TypeByName("MJM.SystemUI.MessageDialog");
        var instances = dialogType == null
            ? null
            : AccessTools.Field(dialogType, "instances")?.GetValue(null) as IList;
        if (instances == null)
        {
            return false;
        }

        for (var dialogIndex = instances.Count - 1; dialogIndex >= 0; dialogIndex -= 1)
        {
            var dialog = instances[dialogIndex];
            if (dialog == null || Convert.ToBoolean(GetMember(dialog, "disposed")))
            {
                continue;
            }
            var buttons = FieldValue(dialog, "buttons") as IList;
            if (buttons == null)
            {
                continue;
            }
            foreach (var button in buttons.Cast<object>())
            {
                var buttonType = GetMember(button, "Type");
                if (buttonType?.ToString() != requestedButton)
                {
                    continue;
                }
                var onPush = FieldValue(dialog, "OnPush");
                var exec = onPush == null ? null : AccessTools.Method(onPush.GetType(), "Exec");
                if (exec == null)
                {
                    return false;
                }
                exec.Invoke(onPush, new[] { buttonType });
                var onPushAnimEnded = FieldValue(dialog, "OnPushAnimEnded");
                var animExec = onPushAnimEnded == null ? null : AccessTools.Method(onPushAnimEnded.GetType(), "Exec");
                animExec?.Invoke(onPushAnimEnded, new[] { buttonType });
                ProbeLog.Write(eventType, DialogPayload(dialog, buttons, requestedButton, dialogIndex));
                return true;
            }
        }
        return false;
    }

    private static object DialogPayload(object dialog, IList buttons, string requestedButton, int dialogIndex)
    {
        return new
        {
            button = requestedButton,
            scene = SceneManager.GetActiveScene().name,
            dialogIndex,
            dialogType = dialog.GetType().FullName,
            disposed = SafeMemberText(dialog, "disposed"),
            buttons = buttons.Cast<object>()
                .Select(button => SafeMemberText(button, "Type"))
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .ToArray(),
            stringMembers = StringMembers(dialog).Take(24).ToArray(),
            casinoSequence = CurrentInnerSequence(FindManager("Casino.CasinoSequenceManager"))?.GetType().FullName,
            menuSequence = CurrentInnerSequence(FindManager("MenuSequenceManager"))?.GetType().FullName,
            loadedScenes = LoadedSceneNames()
        };
    }

    private static IEnumerable<object> StringMembers(object instance)
    {
        for (var current = instance.GetType(); current != null; current = current.BaseType)
        {
            foreach (var field in current.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                if (field.FieldType != typeof(string))
                {
                    continue;
                }
                string? value = null;
                try
                {
                    value = field.GetValue(instance) as string;
                }
                catch
                {
                }
                if (!string.IsNullOrWhiteSpace(value))
                {
                    yield return new { name = field.Name, value };
                }
            }
            foreach (var property in current.GetProperties(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                if (property.PropertyType != typeof(string) || property.GetIndexParameters().Length != 0)
                {
                    continue;
                }
                string? value = null;
                try
                {
                    value = property.GetValue(instance) as string;
                }
                catch
                {
                }
                if (!string.IsNullOrWhiteSpace(value))
                {
                    yield return new { name = property.Name, value };
                }
            }
        }
    }

    private static string? SafeMemberText(object instance, string name)
    {
        try
        {
            return GetMember(instance, name)?.ToString();
        }
        catch
        {
            return null;
        }
    }

    private static bool TryFinishKnownDialogSequence(ActiveBridgeCommand item, string scene)
    {
        foreach (var manager in UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .Where(candidate => candidate.GetType().FullName?.Contains("SequenceManager") == true))
        {
            var sequence = CurrentInnerSequence(manager);
            var sequenceName = sequence?.GetType().FullName;
            if (sequence == null || sequenceName == null)
            {
                continue;
            }
            if (!sequenceName.Contains("SafetyLogoutDialogSequence") && !sequenceName.Contains("MessageDialogSequence"))
            {
                continue;
            }

            SetField(sequence, "isFinish", true);
            WriteStage("sequence_dialog_finished", item, scene, new
            {
                manager = manager.GetType().FullName,
                sequence = sequenceName
            });
            return true;
        }
        return false;
    }

    private static string[] LoadedSceneNames()
    {
        var names = new List<string>();
        for (var index = 0; index < SceneManager.sceneCount; index += 1)
        {
            names.Add(SceneManager.GetSceneAt(index).name);
        }
        return names.ToArray();
    }

    private static void WriteStage(string stage, ActiveBridgeCommand item, string? scene, object? detail)
    {
        var statusKey = string.Join("|", stage, item.Stage.ToString(), scene ?? "", detail?.ToString() ?? "");
        if (item.LastStatusKey == statusKey)
        {
            return;
        }
        item.LastStatusKey = statusKey;
        ProbeLog.Write("exit_to_login_stage", new { stage, item.Stage, scene, detail });
    }
}
