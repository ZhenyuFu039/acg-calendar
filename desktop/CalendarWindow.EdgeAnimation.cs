using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace Playtime;

public sealed partial class CalendarWindow
{
    readonly TranslateTransform edgeSlide = new();
    Task edgeAnimationTask = Task.CompletedTask;
    TaskCompletionSource<bool>? edgeAnimationCompletion;
    int edgeAnimationSequence;

    void CancelEdgeAnimation()
    {
        ++edgeAnimationSequence;
        double position = edgeSlide.X, opacity = edgeHandle.Opacity;
        edgeSlide.BeginAnimation(TranslateTransform.XProperty, null); edgeSlide.X = position;
        edgeHandle.BeginAnimation(OpacityProperty, null); edgeHandle.Opacity = opacity;
        edgeAnimationCompletion?.TrySetResult(false);
    }

    void AnimateEdge(double from, double to, bool opening, bool animate, Action complete)
    {
        int sequence = edgeAnimationSequence;
        var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        edgeAnimationCompletion = completion; edgeAnimationTask = completion.Task;
        void Finish()
        {
            if (sequence != edgeAnimationSequence) return;
            edgeSlide.BeginAnimation(TranslateTransform.XProperty, null); edgeSlide.X = to;
            edgeHandle.BeginAnimation(OpacityProperty, null); edgeHandle.Opacity = opening ? 0 : 1;
            complete(); completion.TrySetResult(true);
        }
        edgeSlide.X = from;
        if (!animate || !SystemParameters.ClientAreaAnimation || Math.Abs(to - from) < 1) { Finish(); return; }
        var duration = TimeSpan.FromMilliseconds(opening ? 280 : 240);
        var movement = new DoubleAnimation(from, to, duration) { EasingFunction = new CubicEase { EasingMode = opening ? EasingMode.EaseOut : EasingMode.EaseInOut } };
        movement.Completed += (_, _) => Finish();
        edgeHandle.BeginAnimation(OpacityProperty, new DoubleAnimation(edgeHandle.Opacity, opening ? 0 : 1, duration));
        edgeSlide.BeginAnimation(TranslateTransform.XProperty, movement);
    }

    void PositionEdgeHandle()
    {
        edgeHandle.Width = 16; edgeHandle.Height = Math.Min(132, expandedBounds.Height);
        edgeHandle.HorizontalAlignment = edgeSide == "left" ? HorizontalAlignment.Left : HorizontalAlignment.Right;
        edgeHandle.VerticalAlignment = VerticalAlignment.Top;
        edgeHandle.Background = Green; ((TextBlock)edgeHandle.Child).Foreground = AccentInk;
    }

    void StartEdgeCollapse()
    {
        if (edgeHidden || edgeTransition || !edgeAutoHide || !compact || sidebarOpen) return;
        CancelEdgeAnimation();
        var area = DesktopWorkArea(); double oldLeft = Left;
        expandedBounds = new Rect(Left, Top, Width, Height);
        edgeSide = Left + Width / 2 < area.Left + area.Width / 2 ? "left" : "right";
        expandedBounds.X = edgeSide == "left" ? area.Left : area.Right - Width;
        edgeTransition = true; edgeHidden = true; edgeLeaveSince = null;
        Left = expandedBounds.Left;
        PositionEdgeHandle(); edgeHandle.Visibility = Visibility.Visible; edgeHandle.Opacity = 0;
        double target = (edgeSide == "left" ? -1 : 1) * (expandedBounds.Width - 16);
        AnimateEdge(oldLeft - Left, target, false, true, () =>
        {
            desktopRoot.Visibility = Visibility.Collapsed;
            MinWidth = 16; MinHeight = 80; Width = 16; Height = edgeHandle.Height;
            Left = edgeSide == "left" ? area.Left : area.Right - 16;
            Top = Math.Clamp(expandedBounds.Top, area.Top, area.Bottom - Height);
            edgeSlide.X = 0; ShowInTaskbar = false; edgeTransition = false;
        });
    }

    void StartEdgeReveal(bool animate)
    {
        if (!edgeHidden && !edgeTransition) return;
        bool interrupting = edgeTransition;
        double position = edgeSlide.X, oldLeft = Left;
        CancelEdgeAnimation();
        edgeTransition = true; edgeHidden = false; edgeLeaveSince = null;
        Width = expandedBounds.Width; Height = expandedBounds.Height;
        Left = expandedBounds.Left; Top = expandedBounds.Top;
        desktopRoot.Visibility = Visibility.Visible;
        PositionEdgeHandle(); edgeHandle.Visibility = Visibility.Visible;
        double from = interrupting ? position + oldLeft - Left : (edgeSide == "left" ? -1 : 1) * (expandedBounds.Width - 16);
        if (!interrupting) edgeHandle.Opacity = 1;
        AnimateEdge(from, 0, true, animate, () =>
        {
            edgeHandle.Visibility = Visibility.Collapsed; edgeHandle.Opacity = 1;
            edgeSlide.X = 0; edgeTransition = false; ApplyPresentation();
        });
    }
}
