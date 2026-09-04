package com.androidagent.client

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import com.androidagent.client.databinding.FragmentGuestGateBinding

/** 游客访问需要服务端的页面时显示的轻量登录引导。 */
class GuestGateFragment : Fragment() {

    private var _binding: FragmentGuestGateBinding? = null
    private val binding get() = _binding!!

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentGuestGateBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val feature = arguments?.getString(ARG_FEATURE).orEmpty()
        binding.textGuestFeature.text = getString(R.string.guest_feature_locked, feature)
        binding.btnGuestLogin.setOnClickListener { MainActivity.startLogin(requireContext()) }
        binding.btnGuestCreative.setOnClickListener {
            (activity as? MainNavActivity)?.selectCreative()
        }
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }

    companion object {
        private const val ARG_FEATURE = "feature"

        fun newInstance(feature: String) = GuestGateFragment().apply {
            arguments = Bundle().apply { putString(ARG_FEATURE, feature) }
        }
    }
}

