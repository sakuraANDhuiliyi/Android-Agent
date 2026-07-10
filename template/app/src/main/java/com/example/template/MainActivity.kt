package com.example.template

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.example.template.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.textView.text = getString(R.string.hello_world)
    }
}
